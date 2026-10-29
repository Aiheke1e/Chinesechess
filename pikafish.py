# pikafish.py — Pikafish 引擎长连接驱动 (~3000 Elo)
# 用法:
#   from pikafish import Pikafish
#   e = Pikafish()
#   mv = e.bestmove(fen, movetime_ms=800)   # 返回 UCI 着法字符串如 "b2e2"
import os
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXE = os.path.join(HERE, "engine_bin", "Pikafish-Windows-x86-64-universal.exe")
NO_CONSOLE = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def parse_info_line(line: str):
    """Parse the useful, public UCI analysis fields from one ``info`` line."""
    parts = line.split()
    if not parts or parts[0] != "info":
        return None
    result = {}
    numeric = {"depth", "seldepth", "multipv", "nodes", "nps", "time",
               "hashfull", "tbhits"}
    index = 1
    while index < len(parts):
        key = parts[index]
        if key in numeric and index + 1 < len(parts):
            try:
                result[key] = int(parts[index + 1])
            except ValueError:
                pass
            index += 2
            continue
        if key == "score" and index + 2 < len(parts):
            kind, value = parts[index + 1:index + 3]
            try:
                result["score_type"] = kind
                result["score"] = int(value)
            except ValueError:
                pass
            index += 3
            if index < len(parts) and parts[index] in ("lowerbound", "upperbound"):
                result["score_bound"] = parts[index]
                index += 1
            continue
        if key == "pv":
            result["pv"] = parts[index + 1:]
            break
        index += 1
    return result if result else None


class Pikafish:
    def __init__(self, exe_path: str = DEFAULT_EXE, cwd: str | None = None):
        if not os.path.exists(exe_path):
            raise FileNotFoundError(f"Pikafish 不存在: {exe_path}")
        self.exe = exe_path
        self.cwd = cwd or os.path.dirname(exe_path)
        self.proc = subprocess.Popen(
            [self.exe], cwd=self.cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            creationflags=NO_CONSOLE)
        self._lines: list[str] = []
        self._diagnostics: list[str] = []
        self._lock = threading.Lock()
        self._command_lock = threading.Lock()
        self.last_analysis: dict = {}
        self.last_candidates: list[dict] = []
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()
        self._err_t = threading.Thread(target=self._stderr_reader, daemon=True)
        self._err_t.start()
        self._handshake()

    def _reader(self):
        while True:
            try:
                line = self.proc.stdout.readline()
            except Exception:
                break
            if not line:
                break
            with self._lock:
                stripped = line.strip()
                self._lines.append(stripped)
                if "ERROR" in stripped.upper() or "FAILED" in stripped.upper():
                    self._diagnostics.append(stripped)
                    self._diagnostics = self._diagnostics[-10:]

    def _stderr_reader(self):
        while True:
            try:
                line = self.proc.stderr.readline()
            except Exception:
                break
            if not line:
                break
            with self._lock:
                self._diagnostics.append(line.strip())
                self._diagnostics = self._diagnostics[-10:]

    def _diagnostic_tail(self):
        with self._lock:
            return " | ".join(item for item in self._diagnostics[-5:] if item)

    def _drain(self):
        with self._lock:
            out = self._lines
            self._lines = []
            return out

    def _wait_for(self, marker, timeout=5.0):
        t0 = time.time()
        got = []
        while time.time() - t0 < timeout:
            lines = self._drain()
            got.extend(lines)
            if any(marker in l for l in lines):
                return True, got
            time.sleep(0.05)
        return False, got

    def _handshake(self):
        """自动适配: 先试 uci, 失败再试 ucci"""
        self.send("uci")
        ok, lines = self._wait_for("uciok", timeout=3.0)
        if not ok:
            # 老式 UCCI 引擎返回 ucciok
            self.send("ucci")
            ok, _ = self._wait_for("ucciok", timeout=3.0)
        if not ok:
            self.quit()
            raise RuntimeError("Pikafish did not complete UCI/UCCI handshake")
        self.send("isready")
        ready, _ = self._wait_for("readyok", timeout=5.0)
        if not ready:
            self.quit()
            raise RuntimeError("Pikafish did not become ready")
        # Spend the same wall-clock budget more effectively.  Two threads are
        # conservative enough to leave CPU for ADB recognition and TTS, while
        # a 128 MB hash prevents the three-PV search from constantly replacing
        # its tiny default table.
        threads = max(1, min(4, int(os.environ.get("PIKAFISH_THREADS", "2"))))
        hash_mb = max(16, min(512, int(os.environ.get(
            "PIKAFISH_HASH_MB", "128"))))
        self.send(f"setoption name Threads value {threads}")
        self.send(f"setoption name Hash value {hash_mb}")
        # 加载 NNUE
        self.send("setoption name EvalFile value pikafish.nnue")
        # Keep three human-readable candidate plans from the same timed search.
        # This does not add a second engine call or delay the board click.
        self.send("setoption name MultiPV value 3")
        return ok

    def send(self, cmd: str):
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def bestmove(self, fen: str, movetime_ms: int = 800, depth: int | None = None) -> str | None:
        """返回 bestmove 字符串 (如 'b2e2'), 失败返回 None"""
        if self.proc.poll() is not None:
            raise RuntimeError("Pikafish process is not running")
        with self._command_lock:
            self._drain()
            self.last_analysis = {}
            self.last_candidates = []
            analysis_by_rank = {}
            self.send(f"position fen {fen}")
            if depth:
                self.send(f"go depth {depth}")
            else:
                self.send(f"go movetime {movetime_ms}")
            t0 = time.time()
            limit = (movetime_ms / 1000.0 + 8.0) if not depth else 30.0
            while time.time() - t0 < limit:
                lines = self._drain()
                for line in lines:
                    info = parse_info_line(line)
                    if info and info.get("pv"):
                        rank = info.get("multipv", 1)
                        previous = analysis_by_rank.get(rank, {})
                        if info.get("depth", 0) >= previous.get("depth", -1):
                            analysis_by_rank[rank] = info
                        if (rank == 1 and info.get("depth", 0) >=
                                self.last_analysis.get("depth", -1)):
                            self.last_analysis = info
                    if line.startswith("bestmove"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] not in ("none", "(none)"):
                            self.last_analysis["bestmove"] = parts[1]
                            self.last_analysis["elapsed_ms"] = int(
                                (time.time() - t0) * 1000)
                            self.last_candidates = [
                                analysis_by_rank[rank]
                                for rank in sorted(analysis_by_rank)[:3]
                                if analysis_by_rank[rank].get("pv")]
                            return parts[1]
                        return None
                if self.proc.poll() is not None:
                    detail = self._diagnostic_tail()
                    raise RuntimeError("Pikafish exited while searching" +
                                       (f": {detail}" if detail else ""))
                time.sleep(0.02)
            self.send("stop")
            self._wait_for("bestmove", timeout=1.0)
            return None

    def quit(self):
        try:
            self.send("quit")
            self.proc.wait(timeout=1.0)
        except Exception:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                pass
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


_engine: Pikafish | None = None


def get_engine(exe_path: str = DEFAULT_EXE) -> Pikafish:
    """全局单例, 复用同一个引擎进程 (避免每步重启)"""
    global _engine
    if (_engine is None or _engine.exe != exe_path or
            _engine.proc.poll() is not None):
        _engine = Pikafish(exe_path)
    return _engine


if __name__ == "__main__":
    e = Pikafish()
    fen = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"
    t0 = time.time()
    print("开局 bestmove:", e.bestmove(fen, movetime_ms=1000), f"({time.time()-t0:.1f}s)")
    # 第二次调用验证复用
    t0 = time.time()
    print("再次 bestmove:", e.bestmove(fen, movetime_ms=1000), f"({time.time()-t0:.1f}s)")
    e.quit()
