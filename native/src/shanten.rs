//! The table-driven shanten kernel.
//!
//! A block's counts, read as a base-five number, index a table of interned
//! distance vectors, and blocks combine through a min-plus merge -- the same
//! scheme as the pure Python kernel, which stays the reference. The tables are
//! parsed from the decompressed payload of the shipped `shanten_blocks.bin`.
//!
//! Inputs are restricted to the tables' radix: at most four copies of a kind,
//! and zero to four melds. The caller routes anything beyond that to the pure
//! path, which prices arbitrary counts recursively.

pub const NUM_KINDS: usize = 34;

const RADIX: usize = 5;
const SUIT_WIDTH: usize = 9;
const HONOR_WIDTH: usize = 7;
const SUIT_SLOTS: usize = 1_953_125; // 5^9
const HONOR_SLOTS: usize = 78_125; // 5^7
const VECTOR_WIDTH: usize = 10;
const MAGIC: &[u8; 4] = b"JSBT";
const SENTINEL: u8 = 255;
const UNREACHED: u8 = 99;
const TILES_PER_KIND: i64 = 4;
const MAX_MELDS: i64 = 4;
const FULL_HAND_SIZE: i64 = 13;

const CHIITOI_TARGET_PAIRS: i64 = 6;
const CHIITOI_DISTINCT: i64 = 7;
const KOKUSHI_KINDS: i64 = 13;
const YAOCHUU: [usize; 13] = [0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33];

/// Each block's start kind and width: three suits of nine ranks, honors of seven.
const BLOCKS: [(usize, usize); 4] = [
    (0, SUIT_WIDTH),
    (9, SUIT_WIDTH),
    (18, SUIT_WIDTH),
    (27, HONOR_WIDTH),
];

fn is_yaochuu(kind: usize) -> bool {
    matches!(kind, 0 | 8 | 9 | 17 | 18 | 26) || kind >= 27
}

/// The resting and holding concealed-tile counts for a hand with this many melds.
fn legal_sizes(num_melds: i64) -> (i64, i64) {
    let rest = FULL_HAND_SIZE - 3 * num_melds;
    (rest, rest + 1)
}

/// The min-plus merge of two distance vectors over shared targets.
fn merge(left: &[u8; VECTOR_WIDTH], right: &[u8; VECTOR_WIDTH]) -> [u8; VECTOR_WIDTH] {
    let mut out = [UNREACHED; VECTOR_WIDTH];
    for left_pair in 0..2 {
        for left_sets in 0..RADIX {
            let left_cost = left[left_pair * RADIX + left_sets];
            for right_pair in 0..(2 - left_pair) {
                for right_sets in 0..(RADIX - left_sets) {
                    let cost = left_cost.saturating_add(right[right_pair * RADIX + right_sets]);
                    let slot = &mut out[(left_pair + right_pair) * RADIX + left_sets + right_sets];
                    if cost < *slot {
                        *slot = cost;
                    }
                }
            }
        }
    }
    out
}

/// One entry of the min-plus merge, for probes that need a single target.
fn merged_entry(left: &[u8; VECTOR_WIDTH], right: &[u8; VECTOR_WIDTH], target: usize) -> u8 {
    let pair = target / RADIX;
    let sets = target % RADIX;
    let mut best = UNREACHED;
    for left_pair in 0..=pair {
        for left_sets in 0..=sets {
            let cost = left[left_pair * RADIX + left_sets]
                .saturating_add(right[(pair - left_pair) * RADIX + (sets - left_sets)]);
            if cost < best {
                best = cost;
            }
        }
    }
    best
}

/// Each block's table index: its counts read as a base-five number.
fn radix_indices(counts: &[u8; NUM_KINDS]) -> [usize; 4] {
    let mut indices = [0usize; 4];
    for (block, &(start, width)) in BLOCKS.iter().enumerate() {
        let mut index = 0usize;
        for offset in (0..width).rev() {
            index = index * RADIX + counts[start + offset] as usize;
        }
        indices[block] = index;
    }
    indices
}

/// Shanten toward seven distinct pairs.
fn chiitoi(counts: &[u8; NUM_KINDS]) -> i64 {
    let mut pairs = 0;
    let mut kinds = 0;
    for &count in counts {
        if count > 0 {
            kinds += 1;
            if count >= 2 {
                pairs += 1;
            }
        }
    }
    CHIITOI_TARGET_PAIRS - pairs + (CHIITOI_DISTINCT - kinds).max(0)
}

/// Shanten toward thirteen orphans.
fn kokushi(counts: &[u8; NUM_KINDS]) -> i64 {
    let mut present = 0;
    let mut has_pair = 0;
    for kind in YAOCHUU {
        if counts[kind] > 0 {
            present += 1;
            if counts[kind] >= 2 {
                has_pair = 1;
            }
        }
    }
    KOKUSHI_KINDS - present - has_pair
}

/// The parsed block tables: interned distance vectors and both class tables.
pub struct Kernel {
    vectors: Vec<[u8; VECTOR_WIDTH]>,
    suit: Vec<u8>,
    honor: Vec<u8>,
}

impl Kernel {
    /// Parse a decompressed table payload, validating shape and class bytes.
    pub fn from_payload(payload: &[u8]) -> Result<Kernel, String> {
        if payload.len() < MAGIC.len() + 1 || &payload[..MAGIC.len()] != MAGIC {
            return Err("shanten table file lacks its magic prefix".into());
        }
        let stored = payload[MAGIC.len()] as usize;
        let vectors_start = MAGIC.len() + 1;
        let suit_start = vectors_start + stored * VECTOR_WIDTH;
        if payload.len() != suit_start + SUIT_SLOTS + HONOR_SLOTS {
            return Err("shanten table file has the wrong length".into());
        }
        let vectors: Vec<[u8; VECTOR_WIDTH]> =
            payload[vectors_start..suit_start].as_chunks().0.to_vec();
        let suit = payload[suit_start..suit_start + SUIT_SLOTS].to_vec();
        let honor = payload[suit_start + SUIT_SLOTS..].to_vec();
        if suit
            .iter()
            .chain(&honor)
            .any(|&cls| cls != SENTINEL && cls as usize >= stored)
        {
            return Err("shanten table file names a vector class it does not store".into());
        }
        Ok(Kernel {
            vectors,
            suit,
            honor,
        })
    }

    /// The distance vector of one block table slot; a sentinel slot is unpriced.
    fn block_vector(&self, table: &[u8], index: usize) -> Result<&[u8; VECTOR_WIDTH], String> {
        let cls = table[index];
        if cls == SENTINEL {
            return Err("shanten tables do not price this block".into());
        }
        Ok(&self.vectors[cls as usize])
    }

    /// The shanten of concealed counts given the meld count.
    ///
    /// Mirrors the pure kernel: seven pairs and thirteen orphans are measured
    /// only for a full concealed hand with no melds. Counts beyond four copies
    /// of a kind are refused; the caller owns that fallback.
    pub fn shanten_counts(&self, counts: &[u8; NUM_KINDS], num_melds: i64) -> Result<i64, String> {
        if !(0..=MAX_MELDS).contains(&num_melds) {
            return Err(format!("melds must be zero to four, got {num_melds}"));
        }
        let total: i64 = counts.iter().map(|&count| count as i64).sum();
        let (resting, holding) = legal_sizes(num_melds);
        if total != resting && total != holding {
            return Err(format!(
                "a hand with {num_melds} melds cannot have {total} concealed tiles"
            ));
        }
        if counts.iter().any(|&count| count as i64 > TILES_PER_KIND) {
            return Err("counts beyond the four-copy radix belong to the pure path".into());
        }
        let indices = radix_indices(counts);
        let manzu = self.block_vector(&self.suit, indices[0])?;
        let pinzu = self.block_vector(&self.suit, indices[1])?;
        let souzu = self.block_vector(&self.suit, indices[2])?;
        let honors = self.block_vector(&self.honor, indices[3])?;
        let target = (RADIX as i64 + MAX_MELDS - num_melds) as usize;
        let suits = merge(&merge(manzu, pinzu), souzu);
        let mut best = merged_entry(&suits, honors, target) as i64 - 1;
        if num_melds == 0 && (total == FULL_HAND_SIZE || total == FULL_HAND_SIZE + 1) {
            best = best.min(chiitoi(counts)).min(kokushi(counts));
        }
        Ok(best)
    }

    /// The shanten after changing each kind's count by one tile, for every kind.
    ///
    /// Mirrors the pure kernel's `probe_shantens`: entry `kind` is the shanten
    /// after the probe, or `None` where the probe would leave that kind's count
    /// outside zero to four.
    pub fn probe_shantens(
        &self,
        counts: &[u8; NUM_KINDS],
        num_melds: i64,
        delta: i64,
    ) -> Result<Vec<Option<i8>>, String> {
        if !(0..=MAX_MELDS).contains(&num_melds) {
            return Err(format!("melds must be zero to four, got {num_melds}"));
        }
        let total: i64 = counts.iter().map(|&count| count as i64).sum();
        let (resting, holding) = legal_sizes(num_melds);
        if total + delta != resting && total + delta != holding {
            return Err(format!(
                "a hand with {num_melds} melds cannot have {} concealed tiles",
                total + delta
            ));
        }
        if counts.iter().any(|&count| count as i64 > TILES_PER_KIND) {
            return Err("probe counts must not exceed four copies of a kind".into());
        }
        let mut result = self.standard_probes(counts, num_melds, delta)?;
        if num_melds == 0
            && (total + delta == FULL_HAND_SIZE || total + delta == FULL_HAND_SIZE + 1)
        {
            closed_probes(counts, delta, &mut result);
        }
        Ok(result)
    }

    /// Every kind's standard shanten after a one-tile probe, by table lookups.
    fn standard_probes(
        &self,
        counts: &[u8; NUM_KINDS],
        num_melds: i64,
        delta: i64,
    ) -> Result<Vec<Option<i8>>, String> {
        let indices = radix_indices(counts);
        let manzu = self.block_vector(&self.suit, indices[0])?;
        let pinzu = self.block_vector(&self.suit, indices[1])?;
        let souzu = self.block_vector(&self.suit, indices[2])?;
        let honors = self.block_vector(&self.honor, indices[3])?;
        let tail = merge(souzu, honors);
        let head = merge(manzu, pinzu);
        let rows = [
            merge(pinzu, &tail),
            merge(manzu, &tail),
            merge(&head, honors),
            merge(&head, souzu),
        ];
        let tables = [&self.suit, &self.suit, &self.suit, &self.honor];
        let target = (RADIX as i64 + MAX_MELDS - num_melds) as usize;
        let mut result = vec![None; NUM_KINDS];
        for (block, &(start, width)) in BLOCKS.iter().enumerate() {
            let row = &rows[block];
            let table = tables[block];
            let mut step = delta;
            for offset in 0..width {
                let kind = start + offset;
                let after = counts[kind] as i64 + delta;
                if (0..=TILES_PER_KIND).contains(&after) {
                    let slot = (indices[block] as i64 + step) as usize;
                    let vector = self.block_vector(table, slot)?;
                    result[kind] = Some(merged_entry(row, vector, target) as i8 - 1);
                }
                step *= RADIX as i64;
            }
        }
        Ok(result)
    }
}

/// Fold seven pairs and thirteen orphans into a meld-free hand's probes.
fn closed_probes(counts: &[u8; NUM_KINDS], delta: i64, result: &mut [Option<i8>]) {
    let mut distinct: i64 = 0;
    let mut pairs: i64 = 0;
    for &count in counts {
        if count > 0 {
            distinct += 1;
            if count >= 2 {
                pairs += 1;
            }
        }
    }
    let mut present: i64 = 0;
    let mut yao_pairs: i64 = 0;
    for kind in YAOCHUU {
        if counts[kind] > 0 {
            present += 1;
            if counts[kind] >= 2 {
                yao_pairs += 1;
            }
        }
    }
    let kokushi_still = KOKUSHI_KINDS - present - i64::from(yao_pairs > 0);
    for (kind, slot) in result.iter_mut().enumerate() {
        let Some(standard) = *slot else { continue };
        let held = counts[kind] as i64;
        let after = held + delta;
        let probed_pairs = pairs + i64::from(after >= 2) - i64::from(held >= 2);
        let missing = CHIITOI_DISTINCT - distinct - i64::from(after > 0) + i64::from(held > 0);
        let chiitoi_probe = CHIITOI_TARGET_PAIRS - probed_pairs + missing.max(0);
        let kokushi_probe = if is_yaochuu(kind) {
            let probed_present = present + i64::from(after > 0) - i64::from(held > 0);
            let probed_yao_pairs = yao_pairs + i64::from(after >= 2) - i64::from(held >= 2);
            KOKUSHI_KINDS - probed_present - i64::from(probed_yao_pairs > 0)
        } else {
            kokushi_still
        };
        *slot = Some((standard as i64).min(chiitoi_probe).min(kokushi_probe) as i8);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A seeded LCG, so tests stay deterministic without a rand dependency.
    fn next(state: &mut u64) -> u64 {
        *state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        *state >> 33
    }

    /// A plausible distance vector: small costs with some unreached entries.
    fn random_vector(state: &mut u64) -> [u8; VECTOR_WIDTH] {
        let mut vector = [0u8; VECTOR_WIDTH];
        for entry in &mut vector {
            let roll = next(state) % 20;
            *entry = if roll >= 15 { UNREACHED } else { roll as u8 };
        }
        vector
    }

    /// The merge written the slow way, straight from the definition.
    fn naive_merge(left: &[u8; VECTOR_WIDTH], right: &[u8; VECTOR_WIDTH]) -> [u8; VECTOR_WIDTH] {
        let mut out = [UNREACHED; VECTOR_WIDTH];
        for (i, &left_cost) in left.iter().enumerate() {
            for (j, &right_cost) in right.iter().enumerate() {
                let (left_pair, left_sets) = (i / RADIX, i % RADIX);
                let (right_pair, right_sets) = (j / RADIX, j % RADIX);
                if left_pair + right_pair > 1 || left_sets + right_sets > MAX_MELDS as usize {
                    continue;
                }
                let index = (left_pair + right_pair) * RADIX + left_sets + right_sets;
                let cost = left_cost.saturating_add(right_cost);
                if cost < out[index] {
                    out[index] = cost;
                }
            }
        }
        out
    }

    #[test]
    fn merge_matches_definition() {
        let mut state = 7u64;
        for _ in 0..500 {
            let left = random_vector(&mut state);
            let right = random_vector(&mut state);
            assert_eq!(merge(&left, &right), naive_merge(&left, &right));
        }
    }

    #[test]
    fn merged_entry_matches_full_merge() {
        let mut state = 11u64;
        for _ in 0..500 {
            let left = random_vector(&mut state);
            let right = random_vector(&mut state);
            let full = merge(&left, &right);
            for (target, &entry) in full.iter().enumerate() {
                assert_eq!(merged_entry(&left, &right, target), entry);
            }
        }
    }

    #[test]
    fn radix_indices_place_digits() {
        let mut counts = [0u8; NUM_KINDS];
        counts[0] = 3; // manzu one -> units digit of block zero
        counts[10] = 2; // pinzu two -> fives digit of block one
        counts[27] = 4; // east -> units digit of the honor block
        let indices = radix_indices(&counts);
        assert_eq!(indices, [3, 10, 0, 4]);
    }

    #[test]
    fn payload_validation_rejects_garbage() {
        assert!(Kernel::from_payload(b"nope").is_err());
        let mut payload = Vec::from(*MAGIC);
        payload.push(0);
        assert!(Kernel::from_payload(&payload).is_err());
        payload.extend(std::iter::repeat_n(1u8, SUIT_SLOTS + HONOR_SLOTS));
        // Class byte 1 with zero stored vectors must be rejected.
        assert!(Kernel::from_payload(&payload).is_err());
    }
}
