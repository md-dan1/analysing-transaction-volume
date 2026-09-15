from collections import deque
from collections.abc import Mapping
import tracemalloc
import sys

class CumulativeWealthGain:
    def __init__(self, two_delta, eth_pricing_mode):
        #tracemalloc.start()

        self.two_delta = two_delta
        self.gain_total = 0
        self.vertex_map = {}
        self.previous_tx = deque()
        self.eth_pricing_mode = eth_pricing_mode

    def calc_gain(self, v1, v2, val):
        # self-transfer are ignored
        if v1 == v2:
            return 0

        u = self.vertex_map.get(v1, 0)
        v = self.vertex_map.get(v2, 0)

        delta_gain = (max(0, u + val) + max(0, v - val)) - (max(0, u) + max(0, v))

        # optimization to reduce memory
        new_u = u + val
        new_v = v - val

        if abs(new_u) < 1e-9:
            self.vertex_map.pop(v1, None)
        else:
            self.vertex_map[v1] = new_u

        if abs(new_v) < 1e-9:
            self.vertex_map.pop(v2, None)
        else:
            self.vertex_map[v2] = new_v

        return delta_gain

    def rollback_txs(self, block):
        block_gain = 0
        for tx in block["transactions"]:
            tx_value = tx["eth_amount"] if self.eth_pricing_mode else tx["usd_value"]
            block_gain += self.calc_gain(tx["to"], tx["from"], tx_value)
        self.gain_total += block_gain


    def execute_txs(self, block):
        block_gain = 0
        for tx in block["transactions"]:
            tx_value = tx["eth_amount"] if self.eth_pricing_mode else tx["usd_value"]
            g = self.calc_gain(tx["from"], tx["to"], tx_value)
            block_gain += g
        self.previous_tx.append({
            "timestamp": block["timestamp"],
            "transactions": block["transactions"],
        })
        self.gain_total += block_gain

    def run_on_block(self, block: dict) -> int:
        current_time = block["timestamp"]
        cutoff_time = current_time - self.two_delta

        while self.previous_tx and self.previous_tx[0]["timestamp"] < cutoff_time:
            self.rollback_txs(self.previous_tx.popleft())
        self.execute_txs(block)

        return self.gain_total

    def get_memory_state(self):
        n_blocks = len(self.previous_tx)
        n_txs = sum(len(b["transactions"]) for b in self.previous_tx)
        n_vertices = len(self.vertex_map)

        seen = set()
        total_bytes = _deep_sizeof(self, seen)

        vertex_bytes = _deep_sizeof(self.vertex_map, set())
        window_bytes = _deep_sizeof(self.previous_tx, set())

        #current, peak = tracemalloc.get_traced_memory()
        current, peak = 0,0
        return {
            "n_vertices": n_vertices,
            "n_blocks_in_window": n_blocks,
            "n_txs_in_window": n_txs,
            "total_bytes": total_bytes,
            "vertex_map_bytes": vertex_bytes,
            "previous_tx_bytes": window_bytes,
            "shared_overlap_bytes": vertex_bytes + window_bytes - total_bytes,
            "bytes_per_vertex": vertex_bytes / n_vertices if n_vertices else 0,
            "bytes_per_tx": window_bytes / n_txs if n_txs else 0,
            "process_current_bytes": current,
            "process_peak_bytes": peak,
            "peak_to_total_ratio": peak / total_bytes if total_bytes else 0,
        }

def _deep_sizeof(obj, seen):
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    size = sys.getsizeof(obj)

    if isinstance(obj, (str, bytes, bytearray, int, float, bool, type(None))):
        return size
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            size += _deep_sizeof(k, seen) + _deep_sizeof(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset, deque)):
        for item in obj:
            size += _deep_sizeof(item, seen)
    if hasattr(obj, "__dict__"):
        size += _deep_sizeof(obj.__dict__, seen)
    return size