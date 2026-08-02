//! The single-player outlook solver.
//!
//! A native port of the pure solver in `jansou.analysis.ev`, which stays the
//! reference: the reachable-hand graph, the unseen pools, and the value sweeps
//! are computed here with the same structure and the same floating-point
//! operation order, so results match the pure solver to the last bit.
//!
//! Pricing winning completions needs the scoring layer, which is not ported:
//! `pricing_entries` lists every completion a graph can win with, the Python
//! side prices them, and `points_curve` takes the worths back in that order.

use std::collections::{HashMap, VecDeque};
use std::hash::Hash;
use std::sync::Arc;

use crate::shanten::{Kernel, NUM_KINDS};

const COMPLETE: i8 = -1;
const READY: i8 = 0;
const TILES_PER_KIND: u8 = 4;

/// Hands the graph cache holds, summed over its entries (mirrors the pure cap).
const GRAPH_CACHE_STATES: usize = 200_000;
/// Expanded hands the memo holds before evicting.
const EXPANSION_CACHE_SIZE: usize = 25_000;
/// Acceptance breadths the memo holds before evicting.
const BREADTH_CACHE_SIZE: usize = 50_000;

type State = [u8; NUM_KINDS];
type ExpansionKey = (State, u8, u8, Option<u32>, bool);
type BreadthKey = (State, u8, u8, i8);
type GraphKey = (State, u8, u8, u32, Option<u32>);

const YONMA_KINDS: [u8; NUM_KINDS] = {
    let mut kinds = [0u8; NUM_KINDS];
    let mut index = 0;
    while index < NUM_KINDS {
        kinds[index] = index as u8;
        index += 1;
    }
    kinds
};

/// Sanma keeps the manzu one and nine and everything from the pinzu on.
const SANMA_KINDS: [u8; 27] = {
    let mut kinds = [0u8; 27];
    let mut index = 1;
    while index < 27 {
        kinds[index] = (index + 7) as u8;
        index += 1;
    }
    kinds
};

/// The kinds present for the player count: 34 for yonma, 27 for sanma.
fn kinds_in_play(player_count: u8) -> Result<&'static [u8], String> {
    match player_count {
        4 => Ok(&YONMA_KINDS),
        3 => Ok(&SANMA_KINDS),
        _ => Err(format!("player count must be 3 or 4, got {player_count}")),
    }
}

/// One hand's shanten, its winning draws, and its improving draws' landings.
struct Expansion {
    base: i8,
    wins: Vec<u8>,
    moves: Vec<(u8, Vec<State>)>,
}

/// The hands reachable from a starting hand, and how each is reached.
pub struct Graph {
    counts: Vec<State>,
    ready: Vec<bool>,
    wins: Vec<Vec<u8>>,
    moves: Vec<Vec<(u8, Vec<u32>)>>,
}

/// Drop an oldest batch once a memo passes its limit, amortising eviction.
fn trim<K: Clone + Eq + Hash, V>(map: &mut HashMap<K, V>, queue: &mut VecDeque<K>, limit: usize) {
    if map.len() <= limit {
        return;
    }
    for _ in 0..limit / 8 {
        if let Some(key) = queue.pop_front() {
            map.remove(&key);
        }
    }
}

/// The solver: a kernel plus the memos the pure solver keeps at module level.
pub struct Solver {
    kernel: Kernel,
    expansions: HashMap<ExpansionKey, Arc<Expansion>>,
    expansion_queue: VecDeque<ExpansionKey>,
    breadths: HashMap<BreadthKey, i64>,
    breadth_queue: VecDeque<BreadthKey>,
    graphs: HashMap<GraphKey, Arc<Graph>>,
    graph_queue: VecDeque<GraphKey>,
    graph_states: usize,
}

impl Solver {
    /// A solver over the given decompressed table payload.
    pub fn from_payload(payload: &[u8]) -> Result<Solver, String> {
        Ok(Solver {
            kernel: Kernel::from_payload(payload)?,
            expansions: HashMap::new(),
            expansion_queue: VecDeque::new(),
            breadths: HashMap::new(),
            breadth_queue: VecDeque::new(),
            graphs: HashMap::new(),
            graph_queue: VecDeque::new(),
            graph_states: 0,
        })
    }

    /// How many draws would lower this hand's shanten, ignoring visible tiles.
    fn acceptance_breadth(
        &mut self,
        counts: &State,
        num_melds: u8,
        player_count: u8,
        base: i8,
    ) -> Result<i64, String> {
        let key = (*counts, num_melds, player_count, base);
        if let Some(&found) = self.breadths.get(&key) {
            return Ok(found);
        }
        let after = self.kernel.probe_shantens(counts, num_melds as i64, 1)?;
        let mut total = 0i64;
        for &kind in kinds_in_play(player_count)? {
            let held = counts[kind as usize];
            if held < TILES_PER_KIND && after[kind as usize].is_some_and(|value| value < base) {
                total += (TILES_PER_KIND - held) as i64;
            }
        }
        self.breadths.insert(key, total);
        self.breadth_queue.push_back(key);
        trim(
            &mut self.breadths,
            &mut self.breadth_queue,
            BREADTH_CACHE_SIZE,
        );
        Ok(total)
    }

    /// The hands a shanten-lowering draw can leave behind, best first if capped.
    fn landings(
        &mut self,
        counts: &mut State,
        num_melds: u8,
        player_count: u8,
        drawn: u8,
        improved: i8,
        beam: Option<u32>,
    ) -> Result<Vec<State>, String> {
        let after = self.kernel.probe_shantens(counts, num_melds as i64, -1)?;
        let mut landed: Vec<State> = Vec::new();
        for &kind in kinds_in_play(player_count)? {
            let index = kind as usize;
            if counts[index] == 0 || after[index] != Some(improved) || kind == drawn {
                continue;
            }
            counts[index] -= 1;
            landed.push(*counts);
            counts[index] += 1;
        }
        if let Some(cap) = beam {
            if landed.len() > cap as usize {
                let mut breadths = Vec::with_capacity(landed.len());
                for state in &landed {
                    breadths.push(self.acceptance_breadth(
                        state,
                        num_melds,
                        player_count,
                        improved,
                    )?);
                }
                let mut order: Vec<usize> = (0..landed.len()).collect();
                // Stable, so ties keep the pure solver's insertion order.
                order.sort_by_key(|&position| std::cmp::Reverse(breadths[position]));
                landed = order
                    .into_iter()
                    .take(cap as usize)
                    .map(|position| landed[position])
                    .collect();
            }
        }
        Ok(landed)
    }

    /// One hand's expansion, memoised whole across graphs.
    fn expand(
        &mut self,
        state: State,
        num_melds: u8,
        player_count: u8,
        beam: Option<u32>,
        deepen: bool,
    ) -> Result<Arc<Expansion>, String> {
        let key = (state, num_melds, player_count, beam, deepen);
        if let Some(found) = self.expansions.get(&key) {
            return Ok(found.clone());
        }
        let mut counts = state;
        let base = self.kernel.shanten_counts(&counts, num_melds as i64)? as i8;
        let after = self.kernel.probe_shantens(&counts, num_melds as i64, 1)?;
        let mut wins = Vec::new();
        let mut moves = Vec::new();
        for &kind in kinds_in_play(player_count)? {
            let index = kind as usize;
            if counts[index] >= TILES_PER_KIND {
                continue;
            }
            let improved = after[index].expect("an in-radix draw always has a probe value");
            if improved == COMPLETE {
                wins.push(kind);
                continue;
            }
            if improved >= base || !deepen {
                continue;
            }
            counts[index] += 1;
            let landed =
                self.landings(&mut counts, num_melds, player_count, kind, improved, beam)?;
            counts[index] -= 1;
            if !landed.is_empty() {
                moves.push((kind, landed));
            }
        }
        let expansion = Arc::new(Expansion { base, wins, moves });
        self.expansions.insert(key, expansion.clone());
        self.expansion_queue.push_back(key);
        trim(
            &mut self.expansions,
            &mut self.expansion_queue,
            EXPANSION_CACHE_SIZE,
        );
        Ok(expansion)
    }

    /// Every hand reachable from this one by `depth` shanten-lowering draws.
    fn build(
        &mut self,
        state: State,
        num_melds: u8,
        player_count: u8,
        depth: u32,
        beam: Option<u32>,
    ) -> Result<Graph, String> {
        let mut index: HashMap<State, u32> = HashMap::from([(state, 0)]);
        let mut order = vec![state];
        let mut levels = vec![0u32];
        let mut ready = Vec::new();
        let mut wins = Vec::new();
        let mut moves = Vec::new();
        let mut cursor = 0usize;
        while cursor < order.len() {
            let level = levels[cursor];
            let expansion =
                self.expand(order[cursor], num_melds, player_count, beam, level < depth)?;
            ready.push(expansion.base <= READY);
            wins.push(expansion.wins.clone());
            let mut edges = Vec::with_capacity(expansion.moves.len());
            for (kind, landed) in &expansion.moves {
                let mut targets = Vec::with_capacity(landed.len());
                for hand in landed {
                    let next = index.len() as u32;
                    let id = *index.entry(*hand).or_insert_with(|| {
                        order.push(*hand);
                        levels.push(level + 1);
                        next
                    });
                    targets.push(id);
                }
                edges.push((*kind, targets));
            }
            moves.push(edges);
            cursor += 1;
        }
        Ok(Graph {
            counts: order,
            ready,
            wins,
            moves,
        })
    }

    /// The reachable-hand graph for this hand, from the cache where possible.
    fn graph(
        &mut self,
        state: State,
        num_melds: u8,
        player_count: u8,
        depth: u32,
        beam: Option<u32>,
    ) -> Result<Arc<Graph>, String> {
        let key = (state, num_melds, player_count, depth, beam);
        if let Some(found) = self.graphs.get(&key) {
            return Ok(found.clone());
        }
        let built = Arc::new(self.build(state, num_melds, player_count, depth, beam)?);
        self.graph_states += built.counts.len();
        self.graphs.insert(key, built.clone());
        self.graph_queue.push_back(key);
        while self.graph_states > GRAPH_CACHE_STATES && self.graphs.len() > 1 {
            if let Some(oldest) = self.graph_queue.pop_front() {
                if let Some(evicted) = self.graphs.remove(&oldest) {
                    self.graph_states -= evicted.counts.len();
                }
            }
        }
        Ok(built)
    }

    /// The ready and win curves for a resting hand, over zero to `max_draws`.
    pub fn solve_curves(
        &mut self,
        counts: &State,
        num_melds: u8,
        max_draws: i64,
        visible: &State,
        player_count: u8,
        beam: Option<u32>,
    ) -> Result<(Vec<f64>, Vec<f64>), String> {
        let (graph, pools, total) =
            self.prepared(counts, num_melds, max_draws, visible, player_count, beam)?;
        let ready = curve(&graph, &pools, total, max_draws, false);
        let win = curve(&graph, &pools, total, max_draws, true);
        Ok((ready, win))
    }

    /// Every completion the graph can win with, as `(state counts, kind)` pairs.
    ///
    /// The order is the graph's own and is what `points_curve` expects its
    /// worths in. Rebuilding the same graph yields the same order.
    pub fn pricing_entries(
        &mut self,
        counts: &State,
        num_melds: u8,
        depth: i64,
        player_count: u8,
        beam: Option<u32>,
    ) -> Result<Vec<(State, u8)>, String> {
        validate(depth, beam)?;
        let graph = self.graph(*counts, num_melds, player_count, depth as u32, beam)?;
        let mut entries = Vec::new();
        for (index, wins) in graph.wins.iter().enumerate() {
            for &kind in wins {
                entries.push((graph.counts[index], kind));
            }
        }
        Ok(entries)
    }

    /// The points curve, given each pricing entry's worth in entry order.
    #[allow(clippy::too_many_arguments)]
    pub fn points_curve(
        &mut self,
        counts: &State,
        num_melds: u8,
        max_draws: i64,
        visible: &State,
        player_count: u8,
        beam: Option<u32>,
        worths: &[f64],
    ) -> Result<Vec<f64>, String> {
        let (graph, pools, total) =
            self.prepared(counts, num_melds, max_draws, visible, player_count, beam)?;
        let mut cursor = 0;
        let mut worth_by_state = Vec::with_capacity(graph.counts.len());
        for wins in &graph.wins {
            let end = cursor + wins.len();
            if end > worths.len() {
                return Err("fewer worths than the graph's pricing entries".into());
            }
            worth_by_state.push(worths[cursor..end].to_vec());
            cursor = end;
        }
        if cursor != worths.len() {
            return Err("more worths than the graph's pricing entries".into());
        }
        let states = graph.counts.len();
        let mut later = (vec![0.0; states], vec![0.0; states]);
        let mut result = vec![0.0];
        for _ in 0..max_draws {
            later = points_sweep(&graph, &pools, total, &later, &worth_by_state);
            result.push(if later.1[0] != 0.0 {
                later.0[0] / later.1[0]
            } else {
                0.0
            });
        }
        Ok(result)
    }

    /// The graph, pools, and pool total behind both solve entry points.
    fn prepared(
        &mut self,
        counts: &State,
        num_melds: u8,
        max_draws: i64,
        visible: &State,
        player_count: u8,
        beam: Option<u32>,
    ) -> Result<(Arc<Graph>, Vec<State>, i64), String> {
        validate(max_draws, beam)?;
        let kinds = kinds_in_play(player_count)?;
        let graph = self.graph(*counts, num_melds, player_count, max_draws as u32, beam)?;
        let (pools, total) = unseen(&graph, visible, kinds)?;
        if total < max_draws {
            return Err(format!(
                "only {total} tiles are unseen, fewer than the {max_draws} draws asked for"
            ));
        }
        Ok((graph, pools, total))
    }
}

/// Reject a negative horizon or an empty beam, mirroring the pure solver.
fn validate(max_draws: i64, beam: Option<u32>) -> Result<(), String> {
    if max_draws < 0 {
        return Err(format!("max draws must not be negative, got {max_draws}"));
    }
    if beam == Some(0) {
        return Err("beam must be at least one, got 0".into());
    }
    Ok(())
}

/// Copies of each kind still unseen from every hand, and the starting total.
fn unseen(graph: &Graph, visible: &State, kinds: &[u8]) -> Result<(Vec<State>, i64), String> {
    let mut pools = Vec::with_capacity(graph.counts.len());
    for (index, counts) in graph.counts.iter().enumerate() {
        let mut pool = [0u8; NUM_KINDS];
        for &kind in kinds {
            let slot = kind as usize;
            let left = TILES_PER_KIND as i64 - visible[slot] as i64 - counts[slot] as i64;
            if left < 0 && index == 0 {
                return Err(format!(
                    "kind {kind} has more copies visible and held than exist"
                ));
            }
            pool[slot] = left.max(0) as u8;
        }
        pools.push(pool);
    }
    let total = pools[0].iter().map(|&copies| copies as i64).sum();
    Ok((pools, total))
}

/// One draw's worth of value, given the value of every state one draw on.
fn sweep(graph: &Graph, pools: &[State], total: i64, later: &[f64], winning: bool) -> Vec<f64> {
    let mut values = vec![0.0; graph.counts.len()];
    for (index, pool) in pools.iter().enumerate() {
        if !winning && graph.ready[index] {
            values[index] = 1.0;
            continue;
        }
        let held = later[index];
        let mut drawn = 0i64;
        let mut value = 0.0f64;
        if winning {
            for &kind in &graph.wins[index] {
                let copies = pool[kind as usize] as i64;
                drawn += copies;
                value += copies as f64;
            }
        }
        for (kind, targets) in &graph.moves[index] {
            let copies = pool[*kind as usize] as i64;
            drawn += copies;
            let mut best = held;
            for &target in targets {
                let landed = later[target as usize];
                if landed > best {
                    best = landed;
                }
            }
            value += copies as f64 * best;
        }
        values[index] = (value + (total - drawn) as f64 * held) / total as f64;
    }
    values
}

/// The whole draws-remaining curve for one measure.
fn curve(graph: &Graph, pools: &[State], total: i64, horizon: i64, winning: bool) -> Vec<f64> {
    let mut values: Vec<f64> = graph
        .ready
        .iter()
        .map(|&state| if !winning && state { 1.0 } else { 0.0 })
        .collect();
    let mut result = vec![values[0]];
    for _ in 0..horizon {
        values = sweep(graph, pools, total, &values, winning);
        result.push(values[0]);
    }
    result
}

/// One draw of expected points, carrying the win odds of the line that earns it.
fn points_sweep(
    graph: &Graph,
    pools: &[State],
    total: i64,
    later: &(Vec<f64>, Vec<f64>),
    worth_by_state: &[Vec<f64>],
) -> (Vec<f64>, Vec<f64>) {
    let (later_points, later_wins) = later;
    let states = graph.counts.len();
    let mut points = vec![0.0; states];
    let mut wins = vec![0.0; states];
    for (index, pool) in pools.iter().enumerate() {
        let (held_points, held_wins) = (later_points[index], later_wins[index]);
        let mut drawn = 0i64;
        let mut earned = 0.0f64;
        let mut won = 0.0f64;
        for (position, &kind) in graph.wins[index].iter().enumerate() {
            let copies = pool[kind as usize] as i64;
            drawn += copies;
            earned += copies as f64 * worth_by_state[index][position];
            won += copies as f64;
        }
        for (kind, targets) in &graph.moves[index] {
            let copies = pool[*kind as usize] as i64;
            drawn += copies;
            let (mut best_points, mut best_wins) = (held_points, held_wins);
            for &target in targets {
                let landed = later_points[target as usize];
                if landed > best_points {
                    best_points = landed;
                    best_wins = later_wins[target as usize];
                }
            }
            earned += copies as f64 * best_points;
            won += copies as f64 * best_wins;
        }
        points[index] = (earned + (total - drawn) as f64 * held_points) / total as f64;
        wins[index] = (won + (total - drawn) as f64 * held_wins) / total as f64;
    }
    (points, wins)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kinds_in_play_matches_player_count() {
        assert_eq!(kinds_in_play(4).unwrap().len(), 34);
        let sanma = kinds_in_play(3).unwrap();
        assert_eq!(sanma.len(), 27);
        assert!(!sanma.contains(&1) && sanma.contains(&0) && sanma.contains(&8));
        assert!(kinds_in_play(5).is_err());
    }

    /// A two-state graph: state zero improves into a ready state one.
    fn tiny_graph() -> Graph {
        Graph {
            counts: vec![[0; NUM_KINDS]; 2],
            ready: vec![false, true],
            wins: vec![vec![], vec![3]],
            moves: vec![vec![(2, vec![1])], vec![]],
        }
    }

    #[test]
    fn ready_curve_absorbs_and_accumulates() {
        let graph = tiny_graph();
        let mut pool = [0u8; NUM_KINDS];
        pool[2] = 2;
        pool[3] = 2;
        let pools = vec![pool, pool];
        // Ten unseen tiles in total; two of them improve state zero.
        let result = curve(&graph, &pools, 10, 2, false);
        assert_eq!(result[0], 0.0);
        assert_eq!(result[1], 0.2);
        // Miss then hit: 0.2 + 0.8 * 0.2.
        assert!((result[2] - 0.36).abs() < 1e-12);
    }

    #[test]
    fn win_curve_needs_ready_then_win() {
        let graph = tiny_graph();
        let mut pool = [0u8; NUM_KINDS];
        pool[2] = 2;
        pool[3] = 2;
        let pools = vec![pool, pool];
        let result = curve(&graph, &pools, 10, 2, true);
        // One draw cannot both reach ready and win.
        assert_eq!(result[1], 0.0);
        // Improve (2/10) then win (2/10).
        assert!((result[2] - 0.04).abs() < 1e-12);
    }

    #[test]
    fn unseen_pools_clamp_and_report() {
        let graph = tiny_graph();
        let mut visible = [0u8; NUM_KINDS];
        visible[0] = 4;
        let kinds = kinds_in_play(4).unwrap();
        let (pools, total) = unseen(&graph, &visible, kinds).unwrap();
        assert_eq!(pools[0][0], 0);
        assert_eq!(total, 33 * 4);
        visible[0] = 5;
        assert!(unseen(&graph, &visible, kinds).is_err());
    }
}
