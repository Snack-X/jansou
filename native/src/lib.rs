//! Native kernels for jansou's analysis layer.
//!
//! The Python side imports this as `jansou_native` and falls back to the pure
//! implementation when the module is absent. Every error surfaces as a Python
//! `ValueError`, matching the pure kernel's messages where behaviour overlaps.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

mod outlook;
mod shanten;

fn as_counts(counts: &[u8]) -> PyResult<[u8; shanten::NUM_KINDS]> {
    counts.try_into().map_err(|_| {
        PyValueError::new_err(format!(
            "counts must have {} entries, got {}",
            shanten::NUM_KINDS,
            counts.len()
        ))
    })
}

/// The table-driven shanten kernel, parsed from the shipped table payload.
///
/// Construct it with the decompressed bytes of `shanten_blocks.bin`. Counts
/// beyond four copies of a kind are refused with a `ValueError`; the caller
/// routes those to the pure path.
#[pyclass(frozen)]
struct ShantenKernel {
    kernel: shanten::Kernel,
}

#[pymethods]
impl ShantenKernel {
    #[new]
    fn new(payload: &[u8]) -> PyResult<Self> {
        shanten::Kernel::from_payload(payload)
            .map(|kernel| Self { kernel })
            .map_err(PyValueError::new_err)
    }

    /// The shanten of concealed counts given the meld count.
    fn shanten_counts(&self, counts: Vec<u8>, num_melds: i64) -> PyResult<i64> {
        self.kernel
            .shanten_counts(&as_counts(&counts)?, num_melds)
            .map_err(PyValueError::new_err)
    }

    /// The shanten after changing each kind's count by one tile, for every kind.
    fn probe_shantens(
        &self,
        counts: Vec<u8>,
        num_melds: i64,
        delta: i64,
    ) -> PyResult<Vec<Option<i8>>> {
        self.kernel
            .probe_shantens(&as_counts(&counts)?, num_melds, delta)
            .map_err(PyValueError::new_err)
    }
}

/// The single-player outlook solver, a native port of `jansou.analysis.ev`.
///
/// Construct it with the decompressed bytes of `shanten_blocks.bin`. It keeps
/// its own graph and expansion caches, so one long-lived instance should serve
/// a process. Pricing stays with the caller: `pricing_entries` lists the
/// completions a graph can win with, and `points_curve` takes their worths
/// back in the same order.
#[pyclass]
struct OutlookSolver {
    solver: outlook::Solver,
}

#[pymethods]
impl OutlookSolver {
    #[new]
    fn new(payload: &[u8]) -> PyResult<Self> {
        outlook::Solver::from_payload(payload)
            .map(|solver| Self { solver })
            .map_err(PyValueError::new_err)
    }

    /// The ready and win curves for a resting hand, over zero to `max_draws`.
    #[pyo3(signature = (counts, num_melds, max_draws, visible, player_count=4, beam=None))]
    fn solve(
        &mut self,
        counts: Vec<u8>,
        num_melds: u8,
        max_draws: i64,
        visible: Vec<u8>,
        player_count: u8,
        beam: Option<u32>,
    ) -> PyResult<(Vec<f64>, Vec<f64>)> {
        self.solver
            .solve_curves(
                &as_counts(&counts)?,
                num_melds,
                max_draws,
                &as_counts(&visible)?,
                player_count,
                beam,
            )
            .map_err(PyValueError::new_err)
    }

    /// Every completion the hand's graph can win with, as `(counts, kind)` pairs.
    #[pyo3(signature = (counts, num_melds, depth, player_count=4, beam=None))]
    fn pricing_entries(
        &mut self,
        counts: Vec<u8>,
        num_melds: u8,
        depth: i64,
        player_count: u8,
        beam: Option<u32>,
    ) -> PyResult<Vec<(Vec<u8>, u8)>> {
        Ok(self
            .solver
            .pricing_entries(&as_counts(&counts)?, num_melds, depth, player_count, beam)
            .map_err(PyValueError::new_err)?
            .into_iter()
            .map(|(state, kind)| (state.to_vec(), kind))
            .collect())
    }

    /// The points curve, given each pricing entry's worth in entry order.
    #[pyo3(signature = (counts, num_melds, max_draws, visible, worths, player_count=4, beam=None))]
    #[allow(clippy::too_many_arguments)]
    fn points_curve(
        &mut self,
        counts: Vec<u8>,
        num_melds: u8,
        max_draws: i64,
        visible: Vec<u8>,
        worths: Vec<f64>,
        player_count: u8,
        beam: Option<u32>,
    ) -> PyResult<Vec<f64>> {
        self.solver
            .points_curve(
                &as_counts(&counts)?,
                num_melds,
                max_draws,
                &as_counts(&visible)?,
                player_count,
                beam,
                &worths,
            )
            .map_err(PyValueError::new_err)
    }
}

#[pymodule]
fn jansou_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<ShantenKernel>()?;
    module.add_class::<OutlookSolver>()?;
    Ok(())
}
