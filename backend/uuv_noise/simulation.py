from __future__ import annotations

import csv
import json
import math
import wave
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import signal


SOURCE_LABELS = {"point": "点源", "line": "线源", "surface": "面源", "volume": "体源"}


def run_case(config: dict[str, Any], source_type: str, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = synthesize(config, source_type)
    write_outputs(data, config, source_type, output_dir)
    return summary(data, source_type, config)


def synthesize(config: dict[str, Any], source_type: str) -> dict[str, Any]:
    rng = np.random.default_rng(int(config["random_seed"]))
    fs = int(config["fs"])
    n = int(round(fs * float(config["duration_s"])))
    t = np.arange(n) / fs
    f = np.fft.rfftfreq(n, 1 / fs)
    f_safe = np.maximum(f, 1.0)

    features = operating_features(config)
    continuous_db = continuous_spectrum(config, f_safe, features)
    machinery_db = machinery_spectrum(config, f_safe, features)
    flow_db = flow_spectrum(config, f_safe, features)

    continuous = noise_from_db(continuous_db, fs, n, rng)
    machinery = noise_from_db(machinery_db, fs, n, rng)
    flow = noise_from_db(flow_db, fs, n, rng)
    envelope = propeller_envelope(t, features, int(config["uuv"]["blade_count"]), rng)
    tonal, tones, line_db = tonal_signal(config, f, t, features, rng)
    source = continuous * envelope + machinery + flow + tonal

    geometry = source_geometry(config, source_type)
    received, propagation = propagate(source, fs, f, config, geometry)
    ambient = ambient_noise(config, fs, n, rng)
    mix = received + ambient
    spectrum_db = 10 * np.log10(np.maximum(welch_psd(source, fs, f), np.finfo(float).tiny))
    metrics = {
        "source_rms_uPa": rms(source),
        "received_target_rms_uPa": rms(received),
        "ambient_rms_uPa": rms(ambient),
        "received_mix_rms_uPa": rms(mix),
        "preview_snr_db": 20 * math.log10(max(rms(received), 1e-12) / max(rms(ambient), 1e-12)),
    }
    return {
        "fs": fs,
        "t": t,
        "f": f,
        "source": source,
        "received": received,
        "ambient": ambient,
        "mix": mix,
        "spectrum_db": spectrum_db,
        "continuous_db": continuous_db,
        "machinery_db": machinery_db,
        "flow_db": flow_db,
        "line_db": line_db,
        "tones": tones,
        "geometry": geometry,
        "propagation": propagation,
        "features": features,
        "metrics": metrics,
        "envelope": envelope,
    }


def operating_features(config: dict[str, Any]) -> dict[str, float]:
    uuv = config["uuv"]
    speed_mps = float(uuv["speed_mps"])
    speed_kn = speed_mps / 0.514444
    displacement_t = float(uuv.get("displacement_t", 1.2))
    prop_d = float(uuv["propeller_diameter_m"])
    pitch = float(uuv.get("propeller_pitch_m", max(0.75 * prop_d, 0.05)))
    rpm_input = float(uuv.get("rpm", 0.0))
    depth = float(uuv["depth_m"])
    blade_count = int(uuv["blade_count"])

    # Paper-inspired slip-ratio chain: speed + displacement + propeller pitch -> shaft rate.
    slip = 10.51 / (speed_kn + 17.94 * max(displacement_t, 0.1) ** -0.04)
    slip = float(np.clip(slip, 0.08, 0.75))
    rpm_from_speed = 60 * speed_mps / max((1 - slip) * pitch, 0.02)
    rpm = rpm_input if rpm_input > 0 else rpm_from_speed
    shaft_hz = rpm / 60
    bpf_hz = blade_count * shaft_hz
    tip_speed = math.pi * prop_d * shaft_hz

    advance = speed_mps / max(shaft_hz * prop_d, 1e-6)
    wake = float(np.clip(0.18 + 0.03 * math.log10(max(displacement_t, 0.2)), 0.05, 0.45))
    vsi = 2.7 * (10 + depth) / max(math.sqrt(1 + (advance / math.pi) ** 2), 0.1)
    vsy = 1.04 * vsi / max(1 - wake, 0.2)
    if speed_mps < vsi:
        cav = 0.15 * speed_mps / max(vsi, 1e-6)
        cav_state = 0.0
    elif speed_mps < vsy:
        cav = (speed_mps - vsi) / max(vsy - vsi, 1e-6)
        cav_state = 1.0
    else:
        cav = 1.0
        cav_state = 2.0

    return {
        "speed_kn": speed_kn,
        "rpm": rpm,
        "rpm_from_speed": rpm_from_speed,
        "slip_ratio": slip,
        "shaft_hz": shaft_hz,
        "bpf_hz": bpf_hz,
        "tip_speed_mps": tip_speed,
        "advance_coef": advance,
        "wake_fraction": wake,
        "cavitation_inception_speed_mps": vsi,
        "cavitation_saturation_speed_mps": vsy,
        "cavitation_activity": float(np.clip(cav, 0.0, 1.0)),
        "cavitation_state": cav_state,
        "uuv_sl_estimate_db": you_yue_sl(speed_kn),
    }


def you_yue_sl(speed_kn: float, critical_kn: float = 5.0) -> float:
    v = max(speed_kn, 0.1)
    if v < critical_kn:
        return 25 * math.log10(v) + 57
    if abs(v - critical_kn) < 1e-9:
        return 94
    return 94 + 0.5 * (v - critical_kn)


def continuous_spectrum(config: dict[str, Any], f: np.ndarray, feat: dict[str, float]) -> np.ndarray:
    uuv = config["uuv"]
    speed_kn = feat["speed_kn"]
    prop_d = float(uuv["propeller_diameter_m"])
    depth = float(uuv["depth_m"])
    displacement = float(uuv.get("displacement_t", 1.2))
    cav = feat["cavitation_activity"]

    f0 = 300 - 5 * (speed_kn - 10) / max(prop_d, 0.05)
    f0 += 0.015 * (10 + depth) ** 2 * max(feat["advance_coef"], 0.05)
    f0 = float(np.clip(f0, 40, 1800))
    sl0 = 70 + 8 * math.log10(max(displacement, 0.2)) + 12 * math.log10(max(speed_kn, 0.5))
    sl0 += 22 * cav + 0.25 * (feat["uuv_sl_estimate_db"] - 75)
    k1 = 9
    k2 = -6 - 4 * cav
    db = np.empty_like(f)
    low = f < f0
    db[low] = sl0 + k1 * np.log2(np.maximum(f0 / np.maximum(f[low], 1), 1))
    db[~low] = sl0 + k2 * np.log2(np.maximum(f[~low] / f0, 1))
    return db - 10 * np.log10(1 + (f / 12000) ** 4)


def machinery_spectrum(config: dict[str, Any], f: np.ndarray, feat: dict[str, float]) -> np.ndarray:
    return 58 + 12 * math.log10(max(feat["rpm"], 60) / 720) - 11 * np.log10(f / 120) - 10 * np.log10(1 + (f / 2500) ** 2)


def flow_spectrum(config: dict[str, Any], f: np.ndarray, feat: dict[str, float]) -> np.ndarray:
    speed = max(float(config["uuv"]["speed_mps"]), 0.1)
    return 54 + 50 * math.log10(speed / 3) - 8 * np.log10(f / 100) - 10 * np.log10(1 + (f / 1800) ** 2)


def noise_from_db(db: np.ndarray, fs: int, n: int, rng: np.random.Generator) -> np.ndarray:
    psd = 10 ** (db / 10)
    spectrum = np.zeros(n, dtype=complex)
    n_pos = len(psd)
    pos = np.arange(1, n_pos - 1 if n % 2 == 0 else n_pos)
    amp = np.sqrt(2 * psd[pos] * fs / n)
    spectrum[pos] = (n / 2) * amp * np.exp(1j * rng.uniform(0, 2 * np.pi, len(pos)))
    spectrum[-pos] = np.conj(spectrum[pos])
    return np.fft.ifft(spectrum).real


def propeller_envelope(t: np.ndarray, feat: dict[str, float], blade_count: int, rng: np.random.Generator) -> np.ndarray:
    period = 1 / max(feat["bpf_hz"], 0.1)
    sigma = 0.16 * period
    env = np.ones_like(t)
    centers = np.arange(0, t[-1] + period, period)
    pattern = np.maximum(rng.normal(np.linspace(0.8, 1.2, blade_count), 0.08), 0.1)
    depth = 0.18 + 0.45 * feat["cavitation_activity"]
    for i, c in enumerate(centers):
        env += depth * pattern[i % blade_count] * np.exp(-0.5 * ((t - c) / sigma) ** 2)
    env += 0.08 * feat["cavitation_activity"] * np.cos(2 * np.pi * feat["shaft_hz"] * t + 0.4)
    return np.maximum(env, 0.05) / max(rms(env), 1e-12)


def tonal_signal(config: dict[str, Any], f: np.ndarray, t: np.ndarray, feat: dict[str, float], rng: np.random.Generator):
    fs = int(config["fs"])
    df = fs / len(t)
    x = np.zeros_like(t)
    psd = np.zeros_like(f)
    tones: list[dict[str, Any]] = []
    specs = []
    for order, level in zip(range(1, 5), [96, 91, 87, 84]):
        specs.append((f"shaft_{order}X", order * feat["shaft_hz"], level, 0.04))
    for order, level in zip(range(1, 7), [108, 103, 99, 95, 92, 89]):
        specs.append((f"bpf_{order}X", order * feat["bpf_hz"], level + 8 * feat["cavitation_activity"], 0.08))
    for order, level in zip([1, 2, 3, 4, 6], [92, 88, 85, 82, 78]):
        specs.append((f"motor_{order}X", order * 120.0, level, 0.02))

    jitter_gain = 0.02 + 0.18 * max(float(config["uuv"]["speed_mps"]) - 3, 0) / 10
    for name, freq, level, base_jitter in specs:
        if freq <= 0 or freq >= fs / 2:
            continue
        jitter = min(freq * (base_jitter + jitter_gain) / 100, 8.0)
        drift = rng.standard_normal(len(t))
        if len(t) > 32:
            b, a = signal.butter(2, min(1.0, 2.0 / (fs / 2)), btype="low")
            drift = signal.filtfilt(b, a, drift)
        drift = drift / max(np.std(drift), 1e-12) * jitter
        phase = 2 * np.pi * np.cumsum(np.maximum(freq + drift, 0.1)) / fs + rng.uniform(0, 2 * np.pi)
        power = 10 ** (level / 10)
        x += math.sqrt(2 * power) * np.cos(phase)
        sigma = max(0.5, jitter / 2.355, df)
        g = np.exp(-0.5 * ((f - freq) / sigma) ** 2)
        area = np.sum(g) * df
        if area > 0:
            psd += power * g / area
        tones.append({"name": name, "frequency_hz": freq, "line_level_db_re_1uPa2": level, "jitter_hz": jitter})
    return x, tones, 10 * np.log10(np.maximum(psd, np.finfo(float).tiny))


def source_geometry(config: dict[str, Any], source_type: str) -> dict[str, Any]:
    uuv = config["uuv"]
    src = config["source"]
    length = float(uuv["length_m"])
    radius = float(uuv["diameter_m"]) / 2
    center = np.array([0.0, 0.0, -float(uuv["depth_m"])])
    if source_type == "point":
        local = np.array([[0.0, 0.0, 0.0]])
    elif source_type == "line":
        x = np.linspace(-length / 2, length / 2, int(src["line_elements"]))
        local = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
    elif source_type == "surface":
        xs = np.linspace(-length / 2, length / 2, int(src["surface_axial_elements"]))
        th = np.linspace(0, 2 * np.pi, int(src["surface_circum_elements"]), endpoint=False)
        local = np.array([[x, radius * np.cos(a), radius * np.sin(a)] for x in xs for a in th])
    elif source_type == "volume":
        xs = np.linspace(-length / 2, length / 2, int(src["volume_axial_elements"]))
        rs = np.linspace(0, radius, int(src["volume_radial_elements"]) + 1)[1:]
        th = np.linspace(0, 2 * np.pi, int(src["volume_circum_elements"]), endpoint=False)
        pts = [[x, 0.0, 0.0] for x in xs]
        pts += [[x, r * np.cos(a), r * np.sin(a)] for x in xs for r in rs for a in th]
        local = np.array(pts)
    else:
        raise ValueError(f"bad source type: {source_type}")
    heading = math.radians(float(src.get("heading_deg", 0)))
    rot = np.array([[math.cos(heading), -math.sin(heading), 0], [math.sin(heading), math.cos(heading), 0], [0, 0, 1]])
    xyz = local @ rot.T + center
    taper = 0.55 + 0.45 * np.cos(np.pi * np.clip(local[:, 0] / max(length / 2, 1e-9), -1, 1)) ** 2
    weight = taper / np.sum(taper)
    return {"type": source_type, "num_elements": int(len(xyz)), "center_xyz_m": center.tolist(), "local_xyz_m": local.tolist(), "element_xyz_m": xyz.tolist(), "weight": weight.tolist()}


def propagate(x: np.ndarray, fs: int, f: np.ndarray, config: dict[str, Any], geom: dict[str, Any]):
    rx = np.array([config["receiver"]["x_m"], config["receiver"]["y_m"], config["receiver"]["z_m"]], dtype=float)
    xyz = np.array(geom["element_xyz_m"], dtype=float)
    weights = np.array(geom["weight"], dtype=float)
    ranges = np.maximum(np.linalg.norm(xyz - rx, axis=1), 1.0)
    y = np.zeros_like(x)
    alpha_1k = float(thorp(np.array([1.0]))[0])
    c = float(config.get("propagation", {}).get("sound_speed_mps", 1500.0))
    for r, w in zip(ranges, weights):
        tl = 20 * math.log10(r) + alpha_1k * r / 1000
        delay = int(round(r / c * fs))
        if delay < len(x):
            y[delay:] += math.sqrt(w) * 10 ** (-tl / 20) * x[: len(x) - delay]
    power_gain = np.zeros_like(f)
    for r, w in zip(ranges, weights):
        tl = 20 * np.log10(r) + thorp(f / 1000) * r / 1000
        power_gain += w * 10 ** (-tl / 10)
    return y, {"range_m": float(np.sum(weights * ranges)), "min_range_m": float(np.min(ranges)), "max_range_m": float(np.max(ranges)), "tl_db": (-10 * np.log10(np.maximum(power_gain, np.finfo(float).tiny))).tolist(), "receiver_xyz_m": rx.tolist()}


def thorp(f_khz: np.ndarray) -> np.ndarray:
    f2 = f_khz**2
    a = 0.11 * f2 / (1 + f2) + 44 * f2 / (4100 + f2) + 2.75e-4 * f2 + 0.003
    return np.where(f_khz <= 0, 0, a)


def ambient_noise(config: dict[str, Any], fs: int, n: int, rng: np.random.Generator) -> np.ndarray:
    ambient = config["ambient"]
    if not ambient.get("enabled", True):
        return np.zeros(n)
    f = np.fft.rfftfreq(n, 1 / fs)
    db = float(ambient["slope_db_decade"]) * np.log10(np.maximum(f, 1) / 100)
    x = noise_from_db(db, fs, n, rng)
    return x / max(rms(x), 1e-12) * float(ambient["rms_uPa"])


def welch_psd(x: np.ndarray, fs: int, target_f: np.ndarray) -> np.ndarray:
    nperseg = min(4096, len(x))
    freqs, pxx = signal.welch(x, fs=fs, nperseg=nperseg, noverlap=nperseg // 2, scaling="density")
    return np.interp(target_f, freqs, pxx, left=pxx[0], right=pxx[-1])


def write_outputs(d: dict[str, Any], config: dict[str, Any], source_type: str, out: Path) -> None:
    write_json(out / "web_result_summary.json", summary(d, source_type, config))
    write_json(out / "input_config.json", config)
    write_wav(out / "uuv_source_signal.wav", d["source"], d["fs"])
    write_wav(out / "uuv_received_target.wav", d["received"], d["fs"])
    write_wav(out / "uuv_received_mix.wav", d["mix"], d["fs"])
    write_spectrum_csv(out / "uuv_source_spectrum.csv", d)
    write_tones_csv(out / "uuv_tonal_lines.csv", d)
    write_geometry_csv(out / "uuv_source_geometry.csv", d)
    np.savez_compressed(out / "uuv_source_model_result.npz", fs=d["fs"], time_s=d["t"], source_signal_uPa=d["source"], received_mix_uPa=d["mix"], frequency_hz=d["f"], spectrum_db=d["spectrum_db"])
    write_description(out / "uuv_noise_description.txt", d, source_type)
    plot_spectrum(out / "uuv_source_spectrum.png", d)
    plot_waveforms(out / "uuv_waveforms.png", d)
    plot_spectrogram(out / "uuv_spectrogram.png", d)
    plot_lofar(out / "uuv_lofar.png", d)
    plot_demon(out / "uuv_demon.png", d)
    plot_geometry(out / "uuv_source_geometry_3d.png", d)
    plot_summary(out / "uuv_summary.png", d)


def summary(d: dict[str, Any], source_type: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    files = {
        "summary_png": "uuv_summary.png",
        "spectrum_png": "uuv_source_spectrum.png",
        "waveforms_png": "uuv_waveforms.png",
        "spectrogram_png": "uuv_spectrogram.png",
        "lofar_png": "uuv_lofar.png",
        "demon_png": "uuv_demon.png",
        "geometry_png": "uuv_source_geometry_3d.png",
        "source_wav": "uuv_source_signal.wav",
        "received_target_wav": "uuv_received_target.wav",
        "received_mix_wav": "uuv_received_mix.wav",
        "spectrum_csv": "uuv_source_spectrum.csv",
        "tones_csv": "uuv_tonal_lines.csv",
        "geometry_csv": "uuv_source_geometry.csv",
        "npz_result": "uuv_source_model_result.npz",
        "description_txt": "uuv_noise_description.txt",
        "log_file": "run.log",
    }
    return {
        "source_type": source_type,
        "source_type_label": SOURCE_LABELS.get(source_type, source_type),
        "noise_type": "UUV target radiated noise, Python semi-empirical model",
        "noise_components": ["Gaussian-pulse propeller modulation", "cavitation broadband continuous spectrum", "machinery broadband noise", "hull-flow broadband noise", "shaft/BPF/motor tonal lines with jitter"],
        "notice": "Engineering prototype; default parameters are illustrative and not calibrated to a real UUV.",
        "geometry": {
            "type": source_type,
            "num_elements": d["geometry"]["num_elements"],
            "source_center_xyz_m": d["geometry"]["center_xyz_m"],
            "element_xyz_m": d["geometry"]["element_xyz_m"],
            "weight": d["geometry"]["weight"],
            "uuv_length_m": float(config["uuv"]["length_m"]) if config else float(d["geometry"].get("uuv_length_m", 3.2)),
            "uuv_diameter_m": float(config["uuv"]["diameter_m"]) if config else float(d["geometry"].get("uuv_diameter_m", 0.45)),
            "uuv_depth_m": float(config["uuv"]["depth_m"]) if config else float(d["geometry"].get("uuv_depth_m", 50.0)),
            "receiver_xyz_m": d["propagation"].get("receiver_xyz_m", [600.0, 160.0, -45.0]),
            "range_m": d["propagation"].get("range_m", 0.0),
        },
        "features": d["features"],
        "metrics": d["metrics"],
        "files": files,
    }


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_wav(path: Path, x: np.ndarray, fs: int) -> None:
    peak = max(float(np.max(np.abs(x))), 1e-12)
    pcm = (np.clip(x / peak * 0.95, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(pcm.tobytes())


def write_spectrum_csv(path: Path, d: dict[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frequency_hz", "SL_total_estimated_db", "SL_continuous_db", "SL_machinery_db", "SL_flow_db", "SL_line_db"])
        step = max(1, len(d["f"]) // 50000)
        for i in range(0, len(d["f"]), step):
            w.writerow([d["f"][i], d["spectrum_db"][i], d["continuous_db"][i], d["machinery_db"][i], d["flow_db"][i], d["line_db"][i]])


def write_tones_csv(path: Path, d: dict[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        fields = ["name", "frequency_hz", "line_level_db_re_1uPa2", "jitter_hz"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(d["tones"])


def write_geometry_csv(path: Path, d: dict[str, Any]) -> None:
    xyz = np.array(d["geometry"]["element_xyz_m"])
    weight = np.array(d["geometry"]["weight"])
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["element_index", "x_m", "y_m", "z_m", "power_weight"])
        for i, row in enumerate(xyz, start=1):
            w.writerow([i, row[0], row[1], row[2], weight[i - 1]])


def write_description(path: Path, d: dict[str, Any], source_type: str) -> None:
    path.write_text(
        "\n".join(
            [
                "UUV target radiated-noise Python prototype",
                f"Equivalent source type: {source_type}",
                f"Source elements: {d['geometry']['num_elements']}",
                "Components: continuous spectrum, Gaussian propeller modulation, tonal lines, machinery and flow broadband.",
                f"Shaft frequency: {d['features']['shaft_hz']:.3f} Hz",
                f"Blade-pass frequency: {d['features']['bpf_hz']:.3f} Hz",
                f"Cavitation activity: {d['features']['cavitation_activity']:.3f}",
                f"You-Yue SL estimate: {d['features']['uuv_sl_estimate_db']:.3f} dB",
                "This is not a calibrated real UUV acoustic signature.",
            ]
        ),
        encoding="utf-8",
    )


def plot_spectrum(path: Path, d: dict[str, Any]) -> None:
    mask = (d["f"] >= 5) & (d["f"] <= d["fs"] / 2)
    draw_lines(
        path,
        "UUV Source Spectrum",
        d["f"][mask],
        [
            ("total", d["spectrum_db"][mask], (20, 20, 20)),
            ("continuous", d["continuous_db"][mask], (30, 105, 170)),
            ("machinery", d["machinery_db"][mask], (190, 90, 40)),
            ("flow", d["flow_db"][mask], (60, 140, 85)),
        ],
        x_log=True,
    )


def plot_waveforms(path: Path, d: dict[str, Any]) -> None:
    nshow = min(len(d["t"]), d["fs"] * 2)
    draw_lines(
        path,
        "Waveforms",
        d["t"][:nshow],
        [
            ("source", scaled(d["source"][:nshow]), (30, 105, 170)),
            ("target rx", scaled(d["received"][:nshow]), (60, 140, 85)),
            ("mix", scaled(d["mix"][:nshow]), (190, 90, 40)),
        ],
    )


def plot_spectrogram(path: Path, d: dict[str, Any]) -> None:
    nper = min(1024, len(d["source"]))
    freqs, tt, sxx = signal.spectrogram(d["source"], fs=d["fs"], nperseg=nper, noverlap=nper // 2)
    render_heatmap(path, "Source Spectrogram", tt, freqs / 1000, 10 * np.log10(sxx + 1e-30), y_max=min(8, d["fs"] / 2000))


def plot_lofar(path: Path, d: dict[str, Any]) -> None:
    nper = min(4096, len(d["source"]))
    freqs, tt, sxx = signal.spectrogram(d["source"], fs=d["fs"], nperseg=nper, noverlap=int(0.75 * nper))
    render_heatmap(path, "LOFAR Preview", tt, freqs, 10 * np.log10(sxx + 1e-30), y_max=min(1000, d["fs"] / 2))


def plot_demon(path: Path, d: dict[str, Any]) -> None:
    low = max(80, 2 * d["features"]["bpf_hz"])
    high = min(d["fs"] * 0.45, 8000)
    if low >= high:
        low = max(40, d["features"]["bpf_hz"])
    sos = signal.butter(4, [low, high], btype="bandpass", fs=d["fs"], output="sos")
    xb = signal.sosfiltfilt(sos, d["source"]) if len(d["source"]) > 128 else signal.sosfilt(sos, d["source"])
    env = np.abs(signal.hilbert(xb))
    spec = np.abs(np.fft.rfft((env - np.mean(env)) * np.hanning(len(env))))
    freqs = np.fft.rfftfreq(len(env), 1 / d["fs"])
    mask = freqs <= 500
    draw_lines(path, "DEMON Preview", freqs[mask], [("envelope spectrum", 20 * np.log10(spec[mask] + 1e-18), (20, 20, 20))])


def plot_geometry(path: Path, d: dict[str, Any]) -> None:
    local = np.array(d["geometry"]["local_xyz_m"])
    w = np.array(d["geometry"]["weight"])
    img, draw, plot = make_plot("3D Equivalent Source Geometry")
    xy = local[:, :2]
    xmin, ymin = np.min(xy, axis=0)
    xmax, ymax = np.max(xy, axis=0)
    if xmax == xmin:
        xmax += 1
        xmin -= 1
    if ymax == ymin:
        ymax += 1
        ymin -= 1
    for row, weight in zip(xy, w):
        px = plot[0] + int((row[0] - xmin) / (xmax - xmin) * plot[2])
        py = plot[1] + plot[3] - int((row[1] - ymin) / (ymax - ymin) * plot[3])
        r = max(3, int(4 + 10 * weight / max(w)))
        draw.ellipse((px - r, py - r, px + r, py + r), fill=(30, 105, 170), outline=(255, 255, 255))
    draw.text((plot[0], plot[1] + plot[3] + 16), f"{d['geometry']['type']} source, {d['geometry']['num_elements']} elements", fill=(70, 80, 90))
    img.save(path)


def plot_summary(path: Path, d: dict[str, Any]) -> None:
    img = Image.new("RGB", (1200, 820), "white")
    draw = ImageDraw.Draw(img)
    draw.text((36, 24), "UUV Noise Simulation Summary", fill=(20, 32, 42))
    draw.text((36, 62), f"Shaft {d['features']['shaft_hz']:.2f} Hz | BPF {d['features']['bpf_hz']:.2f} Hz | SNR {d['metrics']['preview_snr_db']:.2f} dB", fill=(70, 80, 90))
    small_line(draw, (60, 130, 540, 330), d["f"], d["spectrum_db"], "spectrum")
    nshow = min(len(d["t"]), d["fs"])
    small_line(draw, (660, 130, 540, 330), d["t"][:nshow], scaled(d["source"][:nshow]), "waveform")
    values = [d["metrics"]["source_rms_uPa"], d["metrics"]["received_target_rms_uPa"], d["metrics"]["ambient_rms_uPa"], d["metrics"]["received_mix_rms_uPa"]]
    labels = ["source", "rx", "amb", "mix"]
    small_bars(draw, (60, 520, 540, 230), values, labels, "RMS")
    tone_x = np.array([r["frequency_hz"] for r in d["tones"]])
    tone_y = np.array([r["line_level_db_re_1uPa2"] for r in d["tones"]])
    small_stem(draw, (660, 520, 540, 230), tone_x, tone_y, "tonal lines")
    img.save(path)


def make_plot(title: str, size: tuple[int, int] = (1200, 760)):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.text((36, 24), title, fill=(20, 32, 42))
    plot = (80, 80, size[0] - 140, size[1] - 150)
    draw.rectangle((plot[0], plot[1], plot[0] + plot[2], plot[1] + plot[3]), outline=(210, 218, 225))
    return img, draw, plot


def draw_lines(path: Path, title: str, x: np.ndarray, series: list[tuple[str, np.ndarray, tuple[int, int, int]]], x_log: bool = False) -> None:
    img, draw, plot = make_plot(title)
    xx = np.asarray(x, dtype=float)
    if x_log:
        xx = np.log10(np.maximum(xx, 1e-9))
    ys = np.concatenate([np.asarray(y, dtype=float) for _, y, _ in series])
    xmin, xmax = finite_range(xx)
    ymin, ymax = finite_range(ys)
    for idx, (name, y, color) in enumerate(series):
        pts = transform_points(xx, np.asarray(y, dtype=float), xmin, xmax, ymin, ymax, plot)
        if len(pts) > 1:
            draw.line(pts, fill=color, width=2)
        draw.text((plot[0] + 12 + 150 * idx, plot[1] + 12), name, fill=color)
    img.save(path)


def render_heatmap(path: Path, title: str, x: np.ndarray, y: np.ndarray, z: np.ndarray, y_max: float) -> None:
    img, draw, plot = make_plot(title)
    mask = y <= y_max
    z2 = z[mask, :] if z.ndim == 2 else z
    z2 = np.nan_to_num(z2, nan=np.nanmin(z2), posinf=np.nanmax(z2), neginf=np.nanmin(z2))
    lo, hi = np.percentile(z2, [5, 98]) if z2.size else (0, 1)
    norm = np.clip((z2 - lo) / max(hi - lo, 1e-9), 0, 1)
    h, w = norm.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[..., 0] = (255 * norm).astype(np.uint8)
    rgb[..., 1] = (180 * np.sqrt(norm)).astype(np.uint8)
    rgb[..., 2] = (255 * (1 - norm)).astype(np.uint8)
    heat = Image.fromarray(np.flipud(rgb)).resize((plot[2], plot[3]))
    img.paste(heat, (plot[0], plot[1]))
    draw.rectangle((plot[0], plot[1], plot[0] + plot[2], plot[1] + plot[3]), outline=(210, 218, 225))
    img.save(path)


def transform_points(x, y, xmin, xmax, ymin, ymax, plot):
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    px = plot[0] + ((x - xmin) / max(xmax - xmin, 1e-9) * plot[2]).astype(int)
    py = plot[1] + plot[3] - ((y - ymin) / max(ymax - ymin, 1e-9) * plot[3]).astype(int)
    return list(zip(px.tolist(), py.tolist()))


def small_line(draw, box, x, y, title):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if title == "spectrum":
        x = np.log10(np.maximum(x, 1))
    xmin, xmax = finite_range(x)
    ymin, ymax = finite_range(y)
    draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), outline=(210, 218, 225))
    draw.text((box[0], box[1] - 24), title, fill=(70, 80, 90))
    pts = transform_points(x, y, xmin, xmax, ymin, ymax, box)
    if len(pts) > 1:
        draw.line(pts, fill=(30, 105, 170), width=2)


def small_stem(draw, box, x, y, title):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(x)
    x = x[order]
    y = y[order]
    xmin, xmax = finite_range(x)
    ymin, ymax = finite_range(np.concatenate([np.array([0.0]), y]))
    draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), outline=(210, 218, 225))
    draw.text((box[0], box[1] - 24), title, fill=(70, 80, 90))
    for xi, yi in zip(x, y):
        px, py = transform_points(np.array([xi]), np.array([yi]), xmin, xmax, ymin, ymax, box)[0]
        _, base = transform_points(np.array([xi]), np.array([0.0]), xmin, xmax, ymin, ymax, box)[0]
        draw.line((px, base, px, py), fill=(30, 105, 170), width=2)
        draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=(30, 105, 170))


def small_bars(draw, box, values, labels, title):
    draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), outline=(210, 218, 225))
    draw.text((box[0], box[1] - 24), title, fill=(70, 80, 90))
    vmax = max(values) or 1
    bar_w = box[2] // (len(values) * 2)
    for i, (v, label) in enumerate(zip(values, labels)):
        x0 = box[0] + 35 + i * bar_w * 2
        h = int(v / vmax * (box[3] - 50))
        draw.rectangle((x0, box[1] + box[3] - h - 28, x0 + bar_w, box[1] + box[3] - 28), fill=(30, 105, 170))
        draw.text((x0, box[1] + box[3] - 22), label, fill=(70, 80, 90))


def finite_range(v: np.ndarray) -> tuple[float, float]:
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0, 1.0
    lo, hi = float(np.min(v)), float(np.max(v))
    if lo == hi:
        return lo - 1, hi + 1
    pad = 0.05 * (hi - lo)
    return lo - pad, hi + pad


def scaled(x: np.ndarray) -> np.ndarray:
    return np.asarray(x) / max(float(np.max(np.abs(x))), 1e-12)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x) ** 2)))
