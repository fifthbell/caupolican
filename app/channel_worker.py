import os
import time
import subprocess
import threading
import shutil
from typing import Optional, List, Tuple
from .hls_utils import mkdir_p, rmrf, atomic_write_text, hardlink_or_copy
import m3u8

class ChannelWorker:
    def __init__(self, channel_id: str, out_root: str, target_duration: int, window_segments: int, max_segments: int = 100):
        self.channel_id = channel_id
        self.out_root = out_root
        self.target_duration = target_duration
        self.window_segments = window_segments
        self.max_segments = max_segments
        self.live_process: Optional[subprocess.Popen] = None
        self.live_url: Optional[str] = None
        self.active = False
        self.restart_count = 0
        self.last_restart = 0
        self.stall_threshold = target_duration * int(os.getenv("STALL_FACTOR", "3"))
        self.running = True
        self.lock = threading.Lock()
        self.media_seq = 0
        self.window: List[Tuple[str, float, bool]] = []
        self.discontinuity = False
        self.current_source = 'standby'
        self.last_processed = 0
        mkdir_p(os.path.join(out_root, "out", channel_id, "segments"))

    def status(self):
        return {
            "active": self.active,
            "media_seq": self.media_seq,
            "window": len(self.window)
        }

    def set_source(self, url: str):
        with self.lock:
            self.stop_source()
            self.live_url = url
            self.restart_count = 0
            self.active = True
            self.current_source = 'live'
            self.discontinuity = True
            self.last_processed = 0
            self._start_live()

    def stop_source(self):
        with self.lock:
            if self.live_process:
                self.live_process.terminate()
                self.live_process.wait()
                self.live_process = None
            self.active = False
            self.live_url = None
            self.current_source = 'standby'
            self.discontinuity = True
            # Clean up input directory
            input_dir = os.path.join(self.out_root, "in", self.channel_id)
            if os.path.exists(input_dir):
                rmrf(input_dir)

    def reset_window(self):
        with self.lock:
            rmrf(os.path.join(self.out_root, "out", self.channel_id, "segments"))
            mkdir_p(os.path.join(self.out_root, "out", self.channel_id, "segments"))
            self.window = []
            self.media_seq = 0
            self.last_processed = 0

    def run(self):
        while self.running:
            time.sleep(0.5)
            with self.lock:
                stalled = self._check_stall()
                source = 'live' if self.active and not stalled else 'standby'
                if source != self.current_source:
                    self.current_source = source
                    self.discontinuity = True
                source_playlist = os.path.join(self.out_root, "in", self.channel_id if source == 'live' else "standby", "index.m3u8")
                if os.path.exists(source_playlist):
                    try:
                        playlist = m3u8.load(source_playlist)
                        for seg in playlist.segments:
                            seg_num = int(seg.uri.split('_')[1].split('.')[0])
                            if seg_num > self.last_processed:
                                src_file = os.path.join(os.path.dirname(source_playlist), seg.uri)
                                dst_file = os.path.join(self.out_root, "out", self.channel_id, "segments", f"{self.media_seq}.ts")
                                if os.path.exists(src_file):
                                    hardlink_or_copy(src_file, dst_file)
                                    self.window.append((f"{self.media_seq}.ts", seg.duration, self.discontinuity))
                                    self.discontinuity = False
                                    self.media_seq += 1
                                    # Clean up old segments when exceeding window or max segments
                                    while len(self.window) > self.window_segments or len(self.window) > self.max_segments:
                                        old_uri, _, _ = self.window.pop(0)
                                        os.remove(os.path.join(self.out_root, "out", self.channel_id, "segments", old_uri))
                                    self.last_processed = seg_num
                        self._write_playlist()
                    except Exception as e:
                        print(f"Error loading playlist {source_playlist}: {e}")

    def _start_live(self):
        if not self.live_url:
            return
        cmd = [
            "ffmpeg",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "2",
            "-rw_timeout", "15000000",
            "-http_persistent", "0",
            "-probesize", "512k",
            "-analyzeduration", "1M",
            "-i", self.live_url,
            "-vf", "scale=-2:1080:flags=bicubic,fps=30",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-g", "60",
            "-keyint_min", "60",
            "-sc_threshold", "0",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "48000",
            "-ac", "2",
            "-f", "hls",
            "-hls_time", str(self.target_duration),
            "-hls_list_size", str(self.window_segments),
            "-hls_flags", "independent_segments+delete_segments+program_date_time",
            "-hls_segment_filename", os.path.join(self.out_root, "in", self.channel_id, "segment_%03d.ts"),
            os.path.join(self.out_root, "in", self.channel_id, "index.m3u8")
        ]
        if "rtmp" in self.live_url:
            cmd.insert(1, "-rw_timeout")
            cmd.insert(2, "15000000")
        mkdir_p(os.path.join(self.out_root, "in", self.channel_id))
        self.live_process = subprocess.Popen(cmd)

    def _handle_crash(self):
        self.stop_source()
        if self.live_url and self.restart_count < int(os.getenv("RESTART_MAX", "6")):
            backoff = min(int(os.getenv("BACKOFF_BASE_MS", "500")) * (int(os.getenv("BACKOFF_FACTOR", "2.0")) ** self.restart_count), int(os.getenv("BACKOFF_CAP_MS", "30000")))
            time.sleep(backoff / 1000)
            self.restart_count += 1
            self._start_live()
        else:
            self.restart_count = 0

    def _check_stall(self):
        if not self.active:
            return False
        playlist_path = os.path.join(self.out_root, "in", self.channel_id, "index.m3u8")
        if os.path.exists(playlist_path):
            return time.time() - os.path.getmtime(playlist_path) > self.stall_threshold
        return True

    def _write_playlist(self):
        if not self.window:
            return
        media_seq = int(self.window[0][0].split('.')[0])
        content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:{self.target_duration}\n#EXT-X-MEDIA-SEQUENCE:{media_seq}\n"
        for uri, duration, disc in self.window:
            if disc:
                content += "#EXT-X-DISCONTINUITY\n"
            content += f"#EXTINF:{duration},\nsegments/{uri}\n"
        atomic_write_text(os.path.join(self.out_root, "out", self.channel_id, "index.m3u8"), content)

    def stop(self):
        self.running = False
        self.stop_source()
