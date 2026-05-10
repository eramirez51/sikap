//! sikap native — Rust hot path for the TUI.
//!
//! Three things live here:
//! 1. `Rasterizer` — paints background, candle bodies + wicks, and axis
//!    labels (via `fontdue`) into an RGBA buffer.
//! 2. `encode_image` — zlib + base64 + chunked APC sequences, the Kitty
//!    graphics-protocol envelope.
//! 3. `delete_all_images_seq` — convenience constant for the periodic purge.
//!
//! OHLC data crosses the Python/Rust boundary as four `array.array('d', …)`
//! buffers — read by Rust as `&[f64]` via the Python buffer protocol, zero
//! copy.

use std::io::Write;
use std::sync::OnceLock;

use base64::Engine;
use base64::engine::general_purpose::STANDARD as B64;
use chrono::{Datelike, TimeZone, Timelike};
use chrono_tz::America::New_York;
use flate2::Compression;
use flate2::write::ZlibEncoder;
use fontdue::{Font, FontSettings};
use pyo3::buffer::PyBuffer;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyTuple};

type Rgba = (u8, u8, u8, u8);

/// The chart's bar↔pixel + price↔pixel transform. Mirrors
/// `core.chart.viewport.Viewport`; PyO3 reads it from any Python object
/// that has these four float attributes (the dataclass we ship).
#[derive(FromPyObject)]
struct Viewport {
    bar_spacing:  f32,
    x_offset:     f32,
    price_scale:  f32,
    price_offset: f32,
}

// ── Embedded font ────────────────────────────────────────────────────

static FONT_BYTES: &[u8] = include_bytes!("../fonts/JetBrainsMono-Regular.ttf");
static FONT: OnceLock<Font> = OnceLock::new();

fn font() -> &'static Font {
    FONT.get_or_init(|| {
        Font::from_bytes(FONT_BYTES, FontSettings::default()).expect("invalid embedded font")
    })
}

// ── Rasterizer ───────────────────────────────────────────────────────

#[pyclass]
struct Rasterizer {
    width:  u32,
    height: u32,
    buf:    Vec<u8>,
}

#[pymethods]
impl Rasterizer {
    #[new]
    fn new(width: u32, height: u32) -> Self {
        let w = width.max(1);
        let h = height.max(1);
        Self { width: w, height: h, buf: vec![0u8; (w * h * 4) as usize] }
    }

    fn resize(&mut self, width: u32, height: u32) {
        let w = width.max(1);
        let h = height.max(1);
        if (w, h) == (self.width, self.height) {
            return;
        }
        self.width  = w;
        self.height = h;
        self.buf    = vec![0u8; (w * h * 4) as usize];
    }

    /// Render the full chart frame.
    ///
    /// `opens` / `highs` / `lows` / `closes` must be the same length and are
    /// expected to be `array.array('d', ...)` (or any object exporting the
    /// Python buffer protocol with f64 items). They're read zero-copy.
    ///
    /// `labels` is a list of `(text, x, y, size_px, (r, g, b, a))` tuples for
    /// axis annotations.
    /// `polylines` is a flat list of `(xs, ys, color, width_px)` tuples in
    /// SoA form: `xs` and `ys` are f64 sequences (or `array.array('d', ...)`
    /// for buffer-protocol speed) of equal length, in chart coords
    /// (bar_index, price). The renderer projects them via `viewport`.
    /// Width currently ignored (1 px).
    /// `rects` is a flat list of `(x1, y1, x2, y2, color)` tuples in chart
    /// coords (bar_index, price). Drawn *between* bg and candles so a
    /// low-alpha fill tints the chart area without obscuring price action.
    /// `chart_w` / `chart_h` are the candle drawing area; everything to the
    /// right and below those bounds is the gutter where axis labels live.
    /// Candle painting is hard-clipped to the candle area so wicks/bodies
    /// can't bleed under the labels.
    #[pyo3(signature = (
        bg, bull, bear, gutter_sep, body_frac,
        bar_width, viewport,
        chart_w, chart_h,
        opens, highs, lows, closes,
        labels,
        polylines,
        rects,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn render<'py>(
        &mut self,
        py: Python<'py>,
        bg:         Rgba,
        bull:       Rgba,
        bear:       Rgba,
        gutter_sep: Rgba,
        body_frac:  f32,
        bar_width:  f32,
        viewport:   Viewport,
        chart_w:    u32,
        chart_h:    u32,
        opens:  &Bound<'py, PyAny>,
        highs:  &Bound<'py, PyAny>,
        lows:   &Bound<'py, PyAny>,
        closes: &Bound<'py, PyAny>,
        labels:    Vec<(String, f32, f32, f32, Rgba)>,
        polylines: &Bound<'py, PyAny>,
        rects:     Vec<(f64, f64, f64, f64, Rgba)>,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let opens_buf  = PyBuffer::<f64>::get(opens)?;
        let highs_buf  = PyBuffer::<f64>::get(highs)?;
        let lows_buf   = PyBuffer::<f64>::get(lows)?;
        let closes_buf = PyBuffer::<f64>::get(closes)?;
        let n = opens_buf.item_count();
        if highs_buf.item_count() != n
            || lows_buf.item_count() != n
            || closes_buf.item_count() != n
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "opens/highs/lows/closes must have equal length",
            ));
        }
        let opens  = opens_buf.to_vec(py)?;
        let highs  = highs_buf.to_vec(py)?;
        let lows   = lows_buf.to_vec(py)?;
        let closes = closes_buf.to_vec(py)?;

        // Background
        fill_all(&mut self.buf, bg);

        // Chart-space tint rectangles (e.g., HVN bands). Drawn after the
        // background and before candles so candles paint over them; with
        // low alpha they look like translucent zones behind the price
        // action. Clipped to the candle area like everything else.
        let clip_w_pre = chart_w.min(self.width);
        let clip_h_pre = chart_h.min(self.height);
        for (x1, y1, x2, y2, color) in &rects {
            if color.3 == 0 {
                continue;
            }
            let x_a = (*x1 as f32) * viewport.bar_spacing + viewport.x_offset;
            let x_b = (*x2 as f32) * viewport.bar_spacing + viewport.x_offset;
            let y_a = (*y1 as f32) * viewport.price_scale + viewport.price_offset;
            let y_b = (*y2 as f32) * viewport.price_scale + viewport.price_offset;
            let x_min = x_a.min(x_b);
            let x_max = x_a.max(x_b);
            let y_min = y_a.min(y_b);
            let y_max = y_a.max(y_b);
            fill_rect_alpha(
                &mut self.buf, self.width, clip_w_pre, clip_h_pre,
                x_min, y_min, x_max - x_min, y_max - y_min, *color,
            );
        }

        // Bodies + wicks — clipped to the candle area so they can't bleed
        // into the gutters under the axis labels.
        let clip_w = chart_w.min(self.width);
        let clip_h = chart_h.min(self.height);
        let body_w    = bar_width * body_frac;
        let half_body = body_w * 0.5;
        for i in 0..n {
            let o = opens[i]  as f32;
            let h = highs[i]  as f32;
            let l = lows[i]   as f32;
            let c = closes[i] as f32;

            let cx    = (i as f32) * viewport.bar_spacing + viewport.x_offset;
            let color = if c >= o { bull } else { bear };

            let body_top    = o * viewport.price_scale + viewport.price_offset;
            let body_bottom = c * viewport.price_scale + viewport.price_offset;
            let wick_top    = h * viewport.price_scale + viewport.price_offset;
            let wick_bottom = l * viewport.price_scale + viewport.price_offset;

            let (b_top, b_bot) = if body_top <= body_bottom {
                (body_top, body_bottom)
            } else {
                (body_bottom, body_top)
            };
            fill_rect(
                &mut self.buf, self.width, clip_w, clip_h,
                cx - half_body, b_top,
                body_w.max(1.0), (b_bot - b_top).max(1.0),
                color,
            );

            let (w_top, w_bot) = if wick_top <= wick_bottom {
                (wick_top, wick_bottom)
            } else {
                (wick_bottom, wick_top)
            };
            fill_rect(
                &mut self.buf, self.width, clip_w, clip_h,
                cx - 0.5, w_top,
                1.0, (w_bot - w_top).max(1.0),
                color,
            );
        }

        // Gutter separator: 1px line at the right and bottom edges of the
        // candle area so the axis frame is visually distinct from the chart.
        if clip_w < self.width {
            fill_rect(
                &mut self.buf, self.width, self.width, self.height,
                clip_w as f32, 0.0, 1.0, clip_h as f32,
                gutter_sep,
            );
        }
        if clip_h < self.height {
            fill_rect(
                &mut self.buf, self.width, self.width, self.height,
                0.0, clip_h as f32, clip_w as f32 + 1.0, 1.0,
                gutter_sep,
            );
        }

        // Indicator polylines — chart-space (bar_index, price) projected via
        // viewport, clipped to the candle area so they can't bleed into the
        // gutter where the axis labels live. Drawn after candles so they sit
        // on top of bodies/wicks; before labels so labels stay legible.
        //
        // Each polyline arrives as a Python tuple `(xs, ys, color, width)`.
        // `xs` and `ys` are buffer-protocol arrays (`array.array('d', …)`)
        // that we read zero-copy via `PyBuffer::as_slice` — critical for
        // pan/zoom perf, since this runs every frame.
        let polylines_list = polylines.downcast::<PyList>()?;
        for item in polylines_list.iter() {
            let tup = item.downcast::<PyTuple>()?;
            if tup.len() < 4 {
                continue;
            }
            let xs_any = tup.get_item(0)?;
            let ys_any = tup.get_item(1)?;
            let color: Rgba = tup.get_item(2)?.extract()?;
            // width currently unused (line is always 1 px); keep extracting
            // so a malformed input still surfaces as an error.
            let _width: f32 = tup.get_item(3)?.extract()?;

            let xs_buf = PyBuffer::<f64>::get(&xs_any)?;
            let ys_buf = PyBuffer::<f64>::get(&ys_any)?;
            let xs_slice = match xs_buf.as_slice(py) {
                Some(s) => s,
                None => continue,    // non-contiguous: skip this polyline
            };
            let ys_slice = match ys_buf.as_slice(py) {
                Some(s) => s,
                None => continue,
            };
            let n = xs_slice.len().min(ys_slice.len());
            if n < 2 {
                continue;
            }
            // Visible bar-index window inferred from the viewport.
            let cw = clip_w as f32;
            let ch = clip_h as f32;
            let (bar_lo_f, bar_hi_f) = if viewport.bar_spacing > 0.0 {
                let inv = 1.0 / viewport.bar_spacing;
                ((-viewport.x_offset) * inv, (cw - viewport.x_offset) * inv)
            } else {
                (f32::NEG_INFINITY, f32::INFINITY)
            };
            // Whole-polyline skip: each polyline is one VWAP session, so
            // its xs run is contiguous. If the run is entirely outside
            // the viewport we drop the whole thing in O(1). Otherwise
            // each segment is checked individually.
            let first_x = xs_slice[0].get() as f32;
            let last_x  = xs_slice[n - 1].get() as f32;
            let (poly_min_x, poly_max_x) = if first_x <= last_x {
                (first_x, last_x)
            } else {
                (last_x, first_x)
            };
            if poly_max_x < bar_lo_f || poly_min_x > bar_hi_f {
                continue;
            }
            for i in 0..n - 1 {
                let bi0 = xs_slice[i].get() as f32;
                let bi1 = xs_slice[i + 1].get() as f32;
                if (bi0 < bar_lo_f && bi1 < bar_lo_f)
                    || (bi0 > bar_hi_f && bi1 > bar_hi_f)
                {
                    continue;
                }
                let p0 = ys_slice[i].get() as f32;
                let p1 = ys_slice[i + 1].get() as f32;
                let x0 = bi0 * viewport.bar_spacing + viewport.x_offset;
                let y0 = p0  * viewport.price_scale + viewport.price_offset;
                let x1 = bi1 * viewport.bar_spacing + viewport.x_offset;
                let y1 = p1  * viewport.price_scale + viewport.price_offset;
                if (y0 < 0.0 && y1 < 0.0) || (y0 >= ch && y1 >= ch) {
                    continue;
                }
                draw_line_aa(
                    &mut self.buf, self.width, clip_w, clip_h,
                    x0, y0, x1, y1, color,
                );
            }
        }

        // Axis labels — clip to the full buffer so they can use the gutter.
        for (text, x, y, size, color) in &labels {
            draw_text(&mut self.buf, self.width, self.height, text, *x, *y, *size, *color);
        }

        Ok(PyBytes::new(py, &self.buf))
    }
}

// ── Kitty graphics protocol encoder ──────────────────────────────────

const CHUNK_SIZE: usize = 4096;

/// Build the full Kitty APC byte sequence for one image transmission.
/// Returns the bytes ready to write to the TTY.
#[pyfunction]
fn encode_image<'py>(
    py: Python<'py>,
    rgba: &Bound<'py, PyAny>,
    width:    u32,
    height:   u32,
    image_id: u32,
    cols:     u32,
    rows:     u32,
) -> PyResult<Bound<'py, PyBytes>> {
    let buf = PyBuffer::<u8>::get(rgba)?;
    let bytes = buf.to_vec(py)?;

    let mut compressed = Vec::with_capacity(bytes.len() / 4);
    {
        let mut enc = ZlibEncoder::new(&mut compressed, Compression::fast());
        enc.write_all(&bytes).map_err(io_err)?;
        enc.finish().map_err(io_err)?;
    }

    let encoded = B64.encode(&compressed);
    let bytes_b64 = encoded.as_bytes();

    let n_chunks = (bytes_b64.len() + CHUNK_SIZE - 1) / CHUNK_SIZE;
    let mut out  = Vec::with_capacity(bytes_b64.len() + 64 * n_chunks);
    for (i, chunk) in bytes_b64.chunks(CHUNK_SIZE).enumerate() {
        let more = if i + 1 < n_chunks { 1 } else { 0 };
        if i == 0 {
            write!(
                out,
                "\x1b_Gf=32,s={width},v={height},a=T,t=d,i={image_id},o=z,c={cols},r={rows},q=2,m={more};",
            )
            .map_err(io_err)?;
        } else {
            write!(out, "\x1b_Gm={more};").map_err(io_err)?;
        }
        out.extend_from_slice(chunk);
        out.extend_from_slice(b"\x1b\\");
    }

    Ok(PyBytes::new(py, &out))
}

#[pyfunction]
fn delete_image_seq(image_id: u32) -> Vec<u8> {
    format!("\x1b_Ga=d,d=I,i={image_id};\x1b\\").into_bytes()
}

#[pyfunction]
fn delete_all_images_seq() -> Vec<u8> {
    b"\x1b_Ga=d,d=a;\x1b\\".to_vec()
}

// ── Module ───────────────────────────────────────────────────────────

#[pymodule]
fn native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Rasterizer>()?;
    m.add_class::<Anchor>()?;
    m.add_function(wrap_pyfunction!(encode_image, m)?)?;
    m.add_function(wrap_pyfunction!(delete_image_seq, m)?)?;
    m.add_function(wrap_pyfunction!(delete_all_images_seq, m)?)?;
    m.add_function(wrap_pyfunction!(compute_vwap, m)?)?;
    Ok(())
}

// ── Pixel ops ────────────────────────────────────────────────────────

fn fill_all(buf: &mut [u8], color: Rgba) {
    for chunk in buf.chunks_exact_mut(4) {
        chunk[0] = color.0;
        chunk[1] = color.1;
        chunk[2] = color.2;
        chunk[3] = color.3;
    }
}

/// `stride_w` is the buffer's row stride in pixels (always the full
/// rasterizer width). `clip_w` / `clip_h` are the right/bottom limits
/// for painting — anything outside is dropped. This separation lets the
/// candle pass paint only into the candle area while still indexing into
/// the same RGBA buffer.
fn fill_rect(
    buf: &mut [u8],
    stride_w: u32,
    clip_w: u32, clip_h: u32,
    x: f32, y: f32, w: f32, h: f32,
    color: Rgba,
) {
    let x0 = x.max(0.0).min(clip_w as f32) as u32;
    let y0 = y.max(0.0).min(clip_h as f32) as u32;
    let x1 = (x + w).max(0.0).min(clip_w as f32) as u32;
    let y1 = (y + h).max(0.0).min(clip_h as f32) as u32;
    if x0 >= x1 || y0 >= y1 {
        return;
    }
    let stride = (stride_w * 4) as usize;
    let row_w  = ((x1 - x0) * 4) as usize;
    for py in y0..y1 {
        let row_start = (py as usize) * stride + (x0 as usize) * 4;
        let row = &mut buf[row_start .. row_start + row_w];
        for chunk in row.chunks_exact_mut(4) {
            chunk[0] = color.0;
            chunk[1] = color.1;
            chunk[2] = color.2;
            chunk[3] = color.3;
        }
    }
}

/// Alpha-blended axis-aligned rectangle fill. Same clip semantics as
/// `fill_rect`, but each pixel is blended over the existing buffer
/// content using the color's alpha. The per-pixel math is unrolled
/// outside the loop so a 1080p tint pass stays within a couple of
/// milliseconds.
fn fill_rect_alpha(
    buf: &mut [u8],
    stride_w: u32,
    clip_w: u32, clip_h: u32,
    x: f32, y: f32, w: f32, h: f32,
    color: Rgba,
) {
    if color.3 == 0 || w <= 0.0 || h <= 0.0 {
        return;
    }
    let x0 = x.max(0.0).min(clip_w as f32) as u32;
    let y0 = y.max(0.0).min(clip_h as f32) as u32;
    let x1 = (x + w).max(0.0).min(clip_w as f32) as u32;
    let y1 = (y + h).max(0.0).min(clip_h as f32) as u32;
    if x0 >= x1 || y0 >= y1 {
        return;
    }
    let stride = (stride_w * 4) as usize;
    let row_w  = ((x1 - x0) * 4) as usize;
    let a   = color.3 as u32;
    let inv = 255 - a;
    let r_pre = color.0 as u32 * a;
    let g_pre = color.1 as u32 * a;
    let b_pre = color.2 as u32 * a;
    for py in y0..y1 {
        let row_start = (py as usize) * stride + (x0 as usize) * 4;
        let row = &mut buf[row_start .. row_start + row_w];
        for chunk in row.chunks_exact_mut(4) {
            chunk[0] = ((r_pre + chunk[0] as u32 * inv) / 255) as u8;
            chunk[1] = ((g_pre + chunk[1] as u32 * inv) / 255) as u8;
            chunk[2] = ((b_pre + chunk[2] as u32 * inv) / 255) as u8;
            chunk[3] = 255;
        }
    }
}

// ── Anchored VWAP compute ────────────────────────────────────────────

/// Session anchor scheme for VWAP. Mirrors the three Python helpers in
/// `core.indicators.vwap` so the public API stays the same.
#[pyclass(eq, eq_int)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum Anchor {
    /// 18:00 ET (CME Globex daily session open). Default for futures.
    GlobexDaily,
    /// 09:30 ET (US regular trading hours open).
    RthUs,
    /// 00:00 UTC.
    UtcDaily,
}

fn anchor_for(a: Anchor, ts: i64) -> i64 {
    match a {
        Anchor::GlobexDaily => et_anchor(ts, 18, 0),
        Anchor::RthUs       => et_anchor(ts,  9, 30),
        Anchor::UtcDaily    => ts - ts.rem_euclid(86_400),
    }
}

/// Most recent New_York-local `(hh:mm)` at or before `ts`. Handles DST
/// via chrono-tz; if the constructed local datetime is non-existent or
/// ambiguous (DST transition window), uses the `latest` interpretation
/// — for 09:30 and 18:00 the transition window is far away so this
/// branch is essentially never taken.
fn et_anchor(ts: i64, hh: u32, mm: u32) -> i64 {
    let utc = chrono::Utc.timestamp_opt(ts, 0).single().expect("ts in range");
    let dt  = utc.with_timezone(&New_York);
    let mut date = dt.date_naive();
    let before_anchor = (dt.hour(), dt.minute()) < (hh, mm);
    if before_anchor {
        date = date - chrono::Duration::days(1);
    }
    let local = date.and_hms_opt(hh, mm, 0).expect("valid hms");
    let resolved = New_York
        .from_local_datetime(&local)
        .latest()
        .or_else(|| New_York.from_local_datetime(&local).earliest())
        .expect("tz resolution");
    resolved.timestamp()
}

/// Anchored VWAP + volume-weighted SD bands.
///
/// Inputs are buffer-protocol arrays (e.g. `array.array('d', ...)`); they're
/// read zero-copy as &[f64] / &[i64].
///
/// Returns `(ts, vwap, std, lower, upper, session_starts)`:
/// - `ts`, `vwap`, `std` of length n.
/// - `lower` / `upper` of length `n_bands * n` (band k at `[k*n .. (k+1)*n]`).
/// - `session_starts`: bar indices where a new anchor session begins. Always
///    starts with 0; the renderer uses these to break overlay polylines so
///    no segment crosses a session reset.
#[pyfunction]
#[pyo3(signature = (ts, opens, highs, lows, closes, volumes, anchor, k_bands))]
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn compute_vwap<'py>(
    py: Python<'py>,
    ts:      &Bound<'py, PyAny>,
    opens:   &Bound<'py, PyAny>,
    highs:   &Bound<'py, PyAny>,
    lows:    &Bound<'py, PyAny>,
    closes:  &Bound<'py, PyAny>,
    volumes: &Bound<'py, PyAny>,
    anchor:  Anchor,
    k_bands: Vec<f64>,
) -> PyResult<(Vec<i64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<i64>)> {
    let _ = opens;  // typical price uses HLC3; opens unused but kept for API symmetry
    let ts_buf  = PyBuffer::<i64>::get(ts)?;
    let h_buf   = PyBuffer::<f64>::get(highs)?;
    let l_buf   = PyBuffer::<f64>::get(lows)?;
    let c_buf   = PyBuffer::<f64>::get(closes)?;
    let v_buf   = PyBuffer::<i64>::get(volumes)?;
    let n = ts_buf.item_count();
    if h_buf.item_count() != n
        || l_buf.item_count() != n
        || c_buf.item_count() != n
        || v_buf.item_count() != n
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "ts/opens/highs/lows/closes/volumes must have equal length",
        ));
    }
    let ts_v = ts_buf.to_vec(py)?;
    let h_v  = h_buf.to_vec(py)?;
    let l_v  = l_buf.to_vec(py)?;
    let c_v  = c_buf.to_vec(py)?;
    let v_v  = v_buf.to_vec(py)?;

    let n_bands = k_bands.len();
    let mut ts_out    = Vec::with_capacity(n);
    let mut vwap_out  = Vec::with_capacity(n);
    let mut std_out   = Vec::with_capacity(n);
    let mut lower_out = vec![0.0_f64; n_bands * n];
    let mut upper_out = vec![0.0_f64; n_bands * n];
    let mut session_starts: Vec<i64> = Vec::new();

    let mut cur_anchor: i64 = i64::MIN;
    let mut sum_v   = 0.0_f64;
    let mut sum_pv  = 0.0_f64;
    let mut sum_ppv = 0.0_f64;

    for i in 0..n {
        let ts_i = ts_v[i];
        let a = anchor_for(anchor, ts_i);
        if a != cur_anchor {
            cur_anchor = a;
            sum_v = 0.0;
            sum_pv = 0.0;
            sum_ppv = 0.0;
            session_starts.push(i as i64);
        }

        let tp = (h_v[i] + l_v[i] + c_v[i]) / 3.0;
        let vol = v_v[i];
        if vol > 0 {
            let v = vol as f64;
            sum_v   += v;
            sum_pv  += tp * v;
            sum_ppv += tp * tp * v;
        }

        let (vwap, std) = if sum_v > 0.0 {
            let vwap = sum_pv / sum_v;
            let var  = sum_ppv / sum_v - vwap * vwap;
            (vwap, if var > 0.0 { var.sqrt() } else { 0.0 })
        } else {
            (tp, 0.0)
        };

        ts_out.push(ts_i);
        vwap_out.push(vwap);
        std_out.push(std);
        for k_idx in 0..n_bands {
            let k = k_bands[k_idx];
            lower_out[k_idx * n + i] = vwap - k * std;
            upper_out[k_idx * n + i] = vwap + k * std;
        }
    }

    Ok((ts_out, vwap_out, std_out, lower_out, upper_out, session_starts))
}

// ── Lines (Wu's antialiased) ─────────────────────────────────────────

/// Plot one pixel of a Wu line, modulating its coverage with the line
/// color's own alpha and clipping to `(clip_w, clip_h)`.
#[inline]
fn plot_aa(
    buf: &mut [u8], stride_w: u32,
    clip_w: u32, clip_h: u32,
    x: i32, y: i32,
    color: Rgba, coverage: f32,
) {
    if x < 0 || y < 0 || (x as u32) >= clip_w || (y as u32) >= clip_h {
        return;
    }
    let cov = coverage.clamp(0.0, 1.0);
    let a   = (cov * color.3 as f32).round() as u8;
    if a == 0 {
        return;
    }
    blend_pixel(buf, stride_w, x as u32, y as u32, color, a);
}

/// Wu's antialiased line. Both endpoints contribute fractional coverage
/// to the two pixels straddling the minor axis; intermediate steps do
/// the same. Single-pixel-wide for now.
fn draw_line_aa(
    buf: &mut [u8], stride_w: u32,
    clip_w: u32, clip_h: u32,
    x0: f32, y0: f32, x1: f32, y1: f32,
    color: Rgba,
) {
    let steep = (y1 - y0).abs() > (x1 - x0).abs();
    let (mut x0, mut y0, mut x1, mut y1) = if steep {
        (y0, x0, y1, x1)
    } else {
        (x0, y0, x1, y1)
    };
    if x0 > x1 {
        std::mem::swap(&mut x0, &mut x1);
        std::mem::swap(&mut y0, &mut y1);
    }
    let dx = x1 - x0;
    let dy = y1 - y0;
    let gradient = if dx == 0.0 { 1.0 } else { dy / dx };

    let plot = |buf: &mut [u8], px: i32, py: i32, cov: f32| {
        if steep {
            plot_aa(buf, stride_w, clip_w, clip_h, py, px, color, cov);
        } else {
            plot_aa(buf, stride_w, clip_w, clip_h, px, py, color, cov);
        }
    };

    // First endpoint
    let xend  = x0.round();
    let yend  = y0 + gradient * (xend - x0);
    let xgap  = 1.0 - (x0 + 0.5).fract().rem_euclid(1.0);
    let xpxl1 = xend as i32;
    let ypxl1 = yend.floor() as i32;
    let yfrac = yend - yend.floor();
    plot(buf, xpxl1, ypxl1,     (1.0 - yfrac) * xgap);
    plot(buf, xpxl1, ypxl1 + 1, yfrac * xgap);
    let mut intery = yend + gradient;

    // Second endpoint
    let xend2  = x1.round();
    let yend2  = y1 + gradient * (xend2 - x1);
    let xgap2  = (x1 + 0.5).fract().rem_euclid(1.0);
    let xpxl2  = xend2 as i32;
    let ypxl2  = yend2.floor() as i32;
    let yfrac2 = yend2 - yend2.floor();
    plot(buf, xpxl2, ypxl2,     (1.0 - yfrac2) * xgap2);
    plot(buf, xpxl2, ypxl2 + 1, yfrac2 * xgap2);

    // Main loop
    for x in (xpxl1 + 1)..xpxl2 {
        let yi = intery.floor() as i32;
        let yf = intery - intery.floor();
        plot(buf, x, yi,     1.0 - yf);
        plot(buf, x, yi + 1, yf);
        intery += gradient;
    }
}

// ── Text ─────────────────────────────────────────────────────────────

fn draw_text(
    buf: &mut [u8],
    width: u32, height: u32,
    text: &str,
    x: f32, y: f32, size_px: f32,
    color: Rgba,
) {
    let f = font();
    let line = match f.horizontal_line_metrics(size_px) {
        Some(l) => l,
        None    => return,
    };
    let baseline_y = y + line.ascent;
    let mut pen_x  = x;
    for ch in text.chars() {
        let (m, bitmap) = f.rasterize(ch, size_px);
        let glyph_x = pen_x + m.xmin as f32;
        let glyph_y = baseline_y - m.height as f32 - m.ymin as f32;
        for row in 0..m.height {
            for col in 0..m.width {
                let alpha = bitmap[row * m.width + col];
                if alpha == 0 {
                    continue;
                }
                let px = (glyph_x + col as f32).floor() as i32;
                let py = (glyph_y + row as f32).floor() as i32;
                if px < 0 || py < 0 || px >= width as i32 || py >= height as i32 {
                    continue;
                }
                blend_pixel(buf, width, px as u32, py as u32, color, alpha);
            }
        }
        pen_x += m.advance_width;
    }
}

#[inline]
fn blend_pixel(buf: &mut [u8], width: u32, x: u32, y: u32, color: Rgba, alpha: u8) {
    let idx = ((y * width + x) * 4) as usize;
    let a   = alpha as u32;
    let inv = 255 - a;
    buf[idx]     = ((color.0 as u32 * a + buf[idx]     as u32 * inv) / 255) as u8;
    buf[idx + 1] = ((color.1 as u32 * a + buf[idx + 1] as u32 * inv) / 255) as u8;
    buf[idx + 2] = ((color.2 as u32 * a + buf[idx + 2] as u32 * inv) / 255) as u8;
    buf[idx + 3] = 255;
}

// ── Helpers ──────────────────────────────────────────────────────────

fn io_err(e: std::io::Error) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
}
