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
use flate2::Compression;
use flate2::write::ZlibEncoder;
use fontdue::{Font, FontSettings};
use pyo3::buffer::PyBuffer;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

type Rgba = (u8, u8, u8, u8);

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
    /// `chart_w` / `chart_h` are the candle drawing area; everything to the
    /// right and below those bounds is the gutter where axis labels live.
    /// Candle painting is hard-clipped to the candle area so wicks/bodies
    /// can't bleed under the labels.
    #[pyo3(signature = (
        bg, bull, bear, gutter_sep, body_frac,
        bar_width, bar_spacing, x_offset,
        price_scale, price_offset,
        chart_w, chart_h,
        opens, highs, lows, closes,
        labels,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn render<'py>(
        &mut self,
        py: Python<'py>,
        bg:           Rgba,
        bull:         Rgba,
        bear:         Rgba,
        gutter_sep:   Rgba,
        body_frac:    f32,
        bar_width:    f32,
        bar_spacing:  f32,
        x_offset:     f32,
        price_scale:  f32,
        price_offset: f32,
        chart_w:      u32,
        chart_h:      u32,
        opens:  &Bound<'py, PyAny>,
        highs:  &Bound<'py, PyAny>,
        lows:   &Bound<'py, PyAny>,
        closes: &Bound<'py, PyAny>,
        labels: Vec<(String, f32, f32, f32, Rgba)>,
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

            let cx    = (i as f32) * bar_spacing + x_offset;
            let color = if c >= o { bull } else { bear };

            let body_top    = o * price_scale + price_offset;
            let body_bottom = c * price_scale + price_offset;
            let wick_top    = h * price_scale + price_offset;
            let wick_bottom = l * price_scale + price_offset;

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
    m.add_function(wrap_pyfunction!(encode_image, m)?)?;
    m.add_function(wrap_pyfunction!(delete_image_seq, m)?)?;
    m.add_function(wrap_pyfunction!(delete_all_images_seq, m)?)?;
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
