from __future__ import annotations

import csv
import json
import math
import wave
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import signal


COMPONENT_LABELS = {
    "wind": "风浪噪声",
    "shipping": "航运噪声",
    "rain": "雨噪声",
    "thermal": "热噪声",
}


def run_environment_case(config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = synthesize_environment(config)
    write_outputs(data, config, output_dir)
    return summary(data, config)


def synthesize_environment(config: dict[str, Any]) -> dict[str, Any]:
    env = config["environment"]
    fs = int(config["fs"])
    n = int(round(fs * float(config["duration_s"])))
    rng = np.random.default_rng(int(config["random_seed"]))
    t = np.arange(n) / fs
    f = np.fft.rfftfreq(n, 1 / fs)
    f_safe = np.maximum(f, 1.0)

    component_db = {
        "wind": wind_wenz_spectrum(f_safe, float(env["wind_speed_mps"])),
        "shipping": shipping_wenz_spectrum(f_safe, float(env["shipping_activity"])),
        "rain": rain_spectrum(f_safe, float(env["rain_rate_mm_h"])),
        "thermal": thermal_wenz_spectrum(f_safe),
    }

    signals: dict[str, np.ndarray] = {}
    selected: list[str] = []
    for name, enabled in env["components"].items():
        if enabled:
            selected.append(name)
            x = noise_from_db(component_db[name] + float(env["gain_db"]), fs, n, rng)
            signals[name] = x
        else:
            signals[name] = np.zeros(n)

    mix = np.sum([signals[name] for name in COMPONENT_LABELS], axis=0)
    mix_db = combine_db([component_db[name] + float(env["gain_db"]) for name in selected]) if selected else np.full_like(f, -300.0)
    metrics = {
        "selected_count": len(selected),
        "mix_rms_uPa": rms(mix),
        "wind_rms_uPa": rms(signals["wind"]),
        "shipping_rms_uPa": rms(signals["shipping"]),
        "rain_rms_uPa": rms(signals["rain"]),
        "thermal_rms_uPa": rms(signals["thermal"]),
        "peak_component": peak_component(signals),
        "peak_component_ascii": peak_component(signals, ascii_label=True),
    }
    return {
        "fs": fs,
        "t": t,
        "f": f,
        "signals": signals,
        "mix": mix,
        "component_db": component_db,
        "mix_db": mix_db,
        "selected": selected,
        "metrics": metrics,
    }


def wind_wenz_spectrum(f_hz: np.ndarray, wind_speed_mps: float) -> np.ndarray:
    f_khz = np.maximum(f_hz / 1000.0, 1e-4)
    wind = max(wind_speed_mps, 0.1)
    return 50.0 + 7.5 * math.sqrt(wind) + 20.0 * np.log10(f_khz) - 40.0 * np.log10(f_khz + 0.4)


def shipping_wenz_spectrum(f_hz: np.ndarray, activity: float) -> np.ndarray:
    f_khz = np.maximum(f_hz / 1000.0, 1e-4)
    ship = float(np.clip(activity, 0.0, 1.0))
    return 40.0 + 20.0 * (ship - 0.5) + 26.0 * np.log10(f_khz) - 60.0 * np.log10(f_khz + 0.03)


def rain_spectrum(f_hz: np.ndarray, rain_rate_mm_h: float) -> np.ndarray:
    f_khz = np.maximum(f_hz / 1000.0, 1e-4)
    rate = max(rain_rate_mm_h, 0.0)
    base = 44.0 + 18.0 * math.log10(rate + 1.0)
    low_bump = 10.0 * np.exp(-0.5 * ((np.log10(f_khz) - np.log10(0.7)) / 0.32) ** 2)
    high_bump = 7.0 * np.exp(-0.5 * ((np.log10(f_khz) - np.log10(8.0)) / 0.28) ** 2)
    slope = -4.0 * np.log10(np.maximum(f_khz, 0.2))
    return base + low_bump + high_bump + slope


def thermal_wenz_spectrum(f_hz: np.ndarray) -> np.ndarray:
    f_khz = np.maximum(f_hz / 1000.0, 1e-4)
    return -15.0 + 20.0 * np.log10(f_khz)


def combine_db(series: list[np.ndarray]) -> np.ndarray:
    if not series:
        return np.array([])
    p = np.zeros_like(series[0], dtype=float)
    for db in series:
        p += 10 ** (db / 10.0)
    return 10.0 * np.log10(np.maximum(p, np.finfo(float).tiny))


def noise_from_db(db: np.ndarray, fs: int, n: int, rng: np.random.Generator) -> np.ndarray:
    psd = 10 ** (db / 10.0)
    spectrum = np.zeros(n, dtype=complex)
    n_pos = len(psd)
    pos = np.arange(1, n_pos - 1 if n % 2 == 0 else n_pos)
    amp = np.sqrt(2 * psd[pos] * fs / n)
    spectrum[pos] = (n / 2) * amp * np.exp(1j * rng.uniform(0, 2 * np.pi, len(pos)))
    spectrum[-pos] = np.conj(spectrum[pos])
    return np.fft.ifft(spectrum).real


def write_outputs(d: dict[str, Any], config: dict[str, Any], out: Path) -> None:
    write_json(out / "environment_result_summary.json", summary(d, config))
    write_json(out / "input_config.json", config)
    write_wav(out / "ocean_environment_mix.wav", d["mix"], d["fs"])
    for name in COMPONENT_LABELS:
        write_wav(out / f"ocean_{name}_noise.wav", d["signals"][name], d["fs"])
    write_spectrum_csv(out / "ocean_environment_spectrum.csv", d)
    write_timeseries_csv(out / "ocean_environment_timeseries.csv", d)
    np.savez_compressed(
        out / "ocean_environment_noise_result.npz",
        fs=d["fs"],
        time_s=d["t"],
        frequency_hz=d["f"],
        mix_signal_uPa=d["mix"],
        mix_psd_db=d["mix_db"],
        wind_signal_uPa=d["signals"]["wind"],
        shipping_signal_uPa=d["signals"]["shipping"],
        rain_signal_uPa=d["signals"]["rain"],
        thermal_signal_uPa=d["signals"]["thermal"],
    )
    write_description(out / "ocean_environment_noise_description.txt", d, config)
    plot_spectrum(out / "ocean_environment_spectrum.png", d)
    plot_waveforms(out / "ocean_environment_waveforms.png", d)
    plot_spectrogram(out / "ocean_environment_spectrogram.png", d)
    plot_summary(out / "ocean_environment_summary.png", d, config)


def summary(d: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    selected_labels = [COMPONENT_LABELS[name] for name in d["selected"]]
    files = {
        "summary_png": "ocean_environment_summary.png",
        "spectrum_png": "ocean_environment_spectrum.png",
        "waveforms_png": "ocean_environment_waveforms.png",
        "spectrogram_png": "ocean_environment_spectrogram.png",
        "mix_wav": "ocean_environment_mix.wav",
        "wind_wav": "ocean_wind_noise.wav",
        "shipping_wav": "ocean_shipping_noise.wav",
        "rain_wav": "ocean_rain_noise.wav",
        "thermal_wav": "ocean_thermal_noise.wav",
        "spectrum_csv": "ocean_environment_spectrum.csv",
        "timeseries_csv": "ocean_environment_timeseries.csv",
        "npz_result": "ocean_environment_noise_result.npz",
        "description_txt": "ocean_environment_noise_description.txt",
        "log_file": "run.log",
    }
    return {
        "module": "environment",
        "source_type": "environment",
        "source_type_label": "海洋背景噪声",
        "noise_type": "Ocean ambient background noise",
        "noise_components": selected_labels,
        "notice": "包含风浪、航运、雨、热噪声四类环境背景噪声。当前实现为 Windows 原生经验谱适配层；可在安装 phonometry/kadlu 后扩展为外部开源包直接后端。",
        "open_source_integration": {
            "primary_candidates": ["phonometry", "kadlu"],
            "windows_status": "phonometry currently requires Python >=3.13 on PyPI; kadlu has GPL-3.0 license and pygrib/eccodes build requirements.",
            "numerical_stack": ["NumPy", "SciPy", "Pillow"],
        },
        "environment": config["environment"],
        "features": {
            "wind_speed_mps": float(config["environment"]["wind_speed_mps"]),
            "shipping_activity": float(config["environment"]["shipping_activity"]),
            "rain_rate_mm_h": float(config["environment"]["rain_rate_mm_h"]),
            "gain_db": float(config["environment"]["gain_db"]),
        },
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
        w.writerow(["frequency_hz", "NL_mix_db", "NL_wind_db", "NL_shipping_db", "NL_rain_db", "NL_thermal_db"])
        step = max(1, len(d["f"]) // 50000)
        for i in range(0, len(d["f"]), step):
            w.writerow([
                d["f"][i],
                d["mix_db"][i],
                d["component_db"]["wind"][i],
                d["component_db"]["shipping"][i],
                d["component_db"]["rain"][i],
                d["component_db"]["thermal"][i],
            ])


def write_timeseries_csv(path: Path, d: dict[str, Any]) -> None:
    step = max(1, len(d["t"]) // 50000)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "mix_uPa", "wind_uPa", "shipping_uPa", "rain_uPa", "thermal_uPa"])
        for i in range(0, len(d["t"]), step):
            w.writerow([
                d["t"][i],
                d["mix"][i],
                d["signals"]["wind"][i],
                d["signals"]["shipping"][i],
                d["signals"]["rain"][i],
                d["signals"]["thermal"][i],
            ])


def write_description(path: Path, d: dict[str, Any], config: dict[str, Any]) -> None:
    selected = ", ".join(COMPONENT_LABELS[name] for name in d["selected"]) or "none"
    path.write_text(
        "\n".join([
            "Ocean ambient background-noise simulation",
            f"Selected components: {selected}",
            f"Wind speed: {config['environment']['wind_speed_mps']} m/s",
            f"Shipping activity: {config['environment']['shipping_activity']} (0-1)",
            f"Rain rate: {config['environment']['rain_rate_mm_h']} mm/h",
            f"Gain: {config['environment']['gain_db']} dB",
            "Outputs include independent component WAV files and a mixed WAV file.",
            "Open-source integration note: this Windows-native build keeps the backend pluggable for phonometry/kadlu; direct package use depends on local Python/package compatibility.",
        ]),
        encoding="utf-8",
    )


def plot_spectrum(path: Path, d: dict[str, Any]) -> None:
    mask = (d["f"] >= 5) & (d["f"] <= d["fs"] / 2)
    draw_lines(
        path,
        "Ocean Ambient Noise Spectrum",
        d["f"][mask],
        [
            ("mix", d["mix_db"][mask], (20, 20, 20)),
            ("wind", d["component_db"]["wind"][mask], (30, 105, 170)),
            ("shipping", d["component_db"]["shipping"][mask], (120, 88, 40)),
            ("rain", d["component_db"]["rain"][mask], (45, 135, 90)),
            ("thermal", d["component_db"]["thermal"][mask], (170, 65, 125)),
        ],
        x_log=True,
    )


def plot_waveforms(path: Path, d: dict[str, Any]) -> None:
    nshow = min(len(d["t"]), d["fs"] * 2)
    draw_lines(
        path,
        "Ocean Ambient Noise Waveforms",
        d["t"][:nshow],
        [
            ("mix", scaled(d["mix"][:nshow]), (20, 20, 20)),
            ("wind", scaled(d["signals"]["wind"][:nshow]), (30, 105, 170)),
            ("shipping", scaled(d["signals"]["shipping"][:nshow]), (120, 88, 40)),
            ("rain", scaled(d["signals"]["rain"][:nshow]), (45, 135, 90)),
            ("thermal", scaled(d["signals"]["thermal"][:nshow]), (170, 65, 125)),
        ],
    )


def plot_spectrogram(path: Path, d: dict[str, Any]) -> None:
    nper = min(1024, len(d["mix"]))
    freqs, tt, sxx = signal.spectrogram(d["mix"], fs=d["fs"], nperseg=nper, noverlap=nper // 2)
    render_heatmap(path, "Ocean Ambient Noise Spectrogram", tt, freqs / 1000, 10 * np.log10(sxx + 1e-30), y_max=min(12, d["fs"] / 2000))


def plot_summary(path: Path, d: dict[str, Any], config: dict[str, Any]) -> None:
    img = Image.new("RGB", (1200, 820), "white")
    draw = ImageDraw.Draw(img)
    draw.text((36, 24), "Ocean Ambient Noise Summary", fill=(20, 32, 42))
    draw.text((36, 62), f"Selected {len(d['selected'])} components | RMS {d['metrics']['mix_rms_uPa']:.2f} uPa | Peak {d['metrics']['peak_component_ascii']}", fill=(70, 80, 90))
    small_line(draw, (60, 130, 540, 330), np.log10(np.maximum(d["f"], 1)), d["mix_db"], "mixed spectrum")
    nshow = min(len(d["t"]), d["fs"])
    small_line(draw, (660, 130, 540, 330), d["t"][:nshow], scaled(d["mix"][:nshow]), "mixed waveform")
    values = [d["metrics"]["wind_rms_uPa"], d["metrics"]["shipping_rms_uPa"], d["metrics"]["rain_rms_uPa"], d["metrics"]["thermal_rms_uPa"]]
    small_bars(draw, (60, 540, 540, 210), values, ["wind", "ship", "rain", "thermal"], "component RMS")
    draw.text((660, 540), "Parameters", fill=(70, 80, 90))
    lines = [
        f"Wind speed: {config['environment']['wind_speed_mps']} m/s",
        f"Shipping activity: {config['environment']['shipping_activity']}",
        f"Rain rate: {config['environment']['rain_rate_mm_h']} mm/h",
        f"Gain: {config['environment']['gain_db']} dB",
    ]
    for i, line in enumerate(lines):
        draw.text((680, 588 + i * 32), line, fill=(30, 41, 59))
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
        draw.text((plot[0] + 12 + 135 * idx, plot[1] + 12), name, fill=color)
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
    rgb[..., 1] = (175 * np.sqrt(norm)).astype(np.uint8)
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
    xmin, xmax = finite_range(x)
    ymin, ymax = finite_range(y)
    draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), outline=(210, 218, 225))
    draw.text((box[0], box[1] - 24), title, fill=(70, 80, 90))
    pts = transform_points(x, y, xmin, xmax, ymin, ymax, box)
    if len(pts) > 1:
        draw.line(pts, fill=(30, 105, 170), width=2)


def small_bars(draw, box, values, labels, title):
    draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), outline=(210, 218, 225))
    draw.text((box[0], box[1] - 24), title, fill=(70, 80, 90))
    vmax = max(values) or 1
    bar_w = box[2] // (len(values) * 2)
    for i, (v, label) in enumerate(zip(values, labels)):
        x0 = box[0] + 35 + i * bar_w * 2
        h = int(v / vmax * (box[3] - 50)) if vmax > 0 else 0
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


def peak_component(signals: dict[str, np.ndarray], ascii_label: bool = False) -> str:
    if not signals:
        return "-"
    name = max(signals, key=lambda key: rms(signals[key]))
    if ascii_label:
        return {"wind": "wind", "shipping": "shipping", "rain": "rain", "thermal": "thermal"}.get(name, name)
    return COMPONENT_LABELS.get(name, name)
