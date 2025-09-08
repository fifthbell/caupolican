import os
import asyncio
import subprocess
import threading
import shutil
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .channel_worker import ChannelWorker
from .hls_utils import mkdir_p, rmrf
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .channel_worker import ChannelWorker
from .hls_utils import mkdir_p, rmrf

app = FastAPI()

# Environment variables
OUT_ROOT = os.getenv("OUT_ROOT", "/out")
TARGET_DURATION = int(os.getenv("TARGET_DURATION", "1"))
WINDOW_SEGMENTS = int(os.getenv("WINDOW_SEGMENTS", "20"))
STANDBY_TEXT = os.getenv("STANDBY_TEXT", "Standby")
STANDBY_IMAGE = os.getenv("STANDBY_IMAGE")
ROUTER_TOKEN = os.getenv("ROUTER_TOKEN")
DISK_USAGE_THRESHOLD = float(os.getenv("DISK_USAGE_THRESHOLD", "0.9"))  # 90% default
MAX_CHANNELS = int(os.getenv("MAX_CHANNELS", "10"))
MAX_SEGMENTS_PER_CHANNEL = int(os.getenv("MAX_SEGMENTS_PER_CHANNEL", "100"))

# Global state
workers: Dict[str, ChannelWorker] = {}
standby_process: Optional[subprocess.Popen] = None
cleanup_task: Optional[asyncio.Task] = None

def check_disk_usage():
    """Check disk usage and clean up if approaching threshold"""
    try:
        # First clean up orphaned segments
        cleanup_orphaned_segments()
        
        stat = shutil.disk_usage(OUT_ROOT)
        usage_ratio = 1 - (stat.free / stat.total)
        if usage_ratio > DISK_USAGE_THRESHOLD:
            print(f"Disk usage {usage_ratio:.2%} exceeds threshold {DISK_USAGE_THRESHOLD:.2%}, cleaning up...")
            # Clean up oldest channels first
            channels_to_cleanup = []
            for channel_id, worker in workers.items():
                if not worker.active:
                    channels_to_cleanup.append(channel_id)
            
            # If no inactive channels, clean up oldest active ones
            if not channels_to_cleanup:
                # Sort by media_seq (oldest first)
                channels_to_cleanup = sorted(workers.keys(), 
                                           key=lambda x: workers[x].media_seq)[:2]  # Clean up 2 oldest
            
            for channel_id in channels_to_cleanup:
                print(f"Cleaning up channel {channel_id}")
                worker = workers[channel_id]
                worker.stop()
                output_dir = os.path.join(OUT_ROOT, "out", channel_id)
                if os.path.exists(output_dir):
                    rmrf(output_dir)
                del workers[channel_id]
            
            # Also clean up orphaned input directories
            in_dir = os.path.join(OUT_ROOT, "in")
            if os.path.exists(in_dir):
                for item in os.listdir(in_dir):
                    item_path = os.path.join(in_dir, item)
                    if os.path.isdir(item_path) and item not in ['standby'] and item not in workers:
                        print(f"Cleaning up orphaned input directory {item}")
                        rmrf(item_path)
                        
    except Exception as e:
        print(f"Error checking disk usage: {e}")

def cleanup_orphaned_segments():
    """Clean up segment files that are not in the current HLS playlists"""
    try:
        in_dir = os.path.join(OUT_ROOT, "in")
        if not os.path.exists(in_dir):
            return
            
        for item in os.listdir(in_dir):
            item_path = os.path.join(in_dir, item)
            if not os.path.isdir(item_path):
                continue
                
            playlist_path = os.path.join(item_path, "index.m3u8")
            if not os.path.exists(playlist_path):
                continue
                
            # Read the playlist to get current segments
            try:
                with open(playlist_path, 'r') as f:
                    content = f.read()
                
                current_segments = set()
                for line in content.split('\n'):
                    if line.endswith('.ts'):
                        current_segments.add(line.strip())
                
                # Get all segment files and sort by modification time (oldest first)
                segment_files = []
                for filename in os.listdir(item_path):
                    if filename.endswith('.ts'):
                        file_path = os.path.join(item_path, filename)
                        if os.path.exists(file_path):
                            mtime = os.path.getmtime(file_path)
                            segment_files.append((file_path, mtime, filename in current_segments))
                
                # Sort by modification time (oldest first)
                segment_files.sort(key=lambda x: x[1])
                
                # For standby, limit total segments regardless of playlist
                if item == 'standby':
                    # Keep only the most recent MAX_SEGMENTS_PER_CHANNEL segments
                    segments_to_keep = MAX_SEGMENTS_PER_CHANNEL
                else:
                    # For other directories, keep segments that are in the playlist
                    segments_to_keep = len(current_segments)
                
                # Remove old segments beyond the limit
                for file_path, mtime, in_playlist in segment_files[:-segments_to_keep]:
                    try:
                        os.remove(file_path)
                        print(f"Cleaned up old segment: {file_path}")
                    except Exception as e:
                        print(f"Error removing {file_path}: {e}")
                        
            except Exception as e:
                print(f"Error processing playlist {playlist_path}: {e}")
                
    except Exception as e:
        print(f"Error in cleanup_orphaned_segments: {e}")

async def periodic_cleanup():
    """Run cleanup periodically"""
    while True:
        await asyncio.sleep(30)  # Run every 30 seconds
        cleanup_orphaned_segments()

class SetSourceRequest(BaseModel):
    url: str

def verify_token(authorization: Optional[str] = Header(None)):
    if ROUTER_TOKEN and (not authorization or authorization != f"Bearer {ROUTER_TOKEN}"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

@app.on_event("startup")
async def startup_event():
    global standby_process, cleanup_task
    mkdir_p(os.path.join(OUT_ROOT, "in", "standby"))
    mkdir_p(OUT_ROOT)
    # Start global standby
    cmd = ["ffmpeg", "-re"]  # -re flag for real-time processing
    if STANDBY_IMAGE:
        cmd += ["-loop", "1", "-i", STANDBY_IMAGE]
    else:
        cmd += ["-f", "lavfi", "-i", "smptebars=size=1920x1080:rate=30"]
    cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    vf = "scale=-2:1080:flags=bicubic,fps=30"
    if not STANDBY_IMAGE:
        vf += ",drawtext=text='%{localtime}':fontsize=72:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5"
    cmd += ["-vf", vf]
    cmd += [
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
        "-hls_time", str(TARGET_DURATION),
        "-hls_list_size", str(WINDOW_SEGMENTS),
        "-hls_flags", "independent_segments+delete_segments+program_date_time",
        "-hls_segment_filename", os.path.join(OUT_ROOT, "in", "standby", "segment_%03d.ts"),
        os.path.join(OUT_ROOT, "in", "standby", "index.m3u8")
    ]
    standby_process = subprocess.Popen(cmd)
    
    # Start periodic cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup())

@app.on_event("shutdown")
async def shutdown_event():
    for worker in workers.values():
        worker.stop()
    if standby_process:
        standby_process.terminate()
        standby_process.wait()
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    # Clean up all directories
    if os.path.exists(OUT_ROOT):
        rmrf(OUT_ROOT)

app.mount("/hls", StaticFiles(directory=OUT_ROOT), name="hls")

@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "channels": {id: worker.status() for id, worker in workers.items()}
    }

@app.get("/api/channels")
async def list_channels():
    return [{"id": id, **worker.status()} for id, worker in workers.items()]

@app.get("/api/channels/{channel_id}/status")
async def channel_status(channel_id: str):
    if channel_id not in workers:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"id": channel_id, **workers[channel_id].status()}

@app.post("/api/channels/{channel_id}/set-source", dependencies=[Depends(verify_token)])
async def set_source(channel_id: str, req: SetSourceRequest):
    # Check channel limit
    if len(workers) >= MAX_CHANNELS and channel_id not in workers:
        raise HTTPException(status_code=429, detail=f"Maximum channels ({MAX_CHANNELS}) reached")
    
    # Check disk usage before creating new channel
    if channel_id not in workers:
        check_disk_usage()
    
    if channel_id not in workers:
        mkdir_p(os.path.join(OUT_ROOT, "out", channel_id))
        worker = ChannelWorker(channel_id, OUT_ROOT, TARGET_DURATION, WINDOW_SEGMENTS, MAX_SEGMENTS_PER_CHANNEL)
        workers[channel_id] = worker
        threading.Thread(target=worker.run, daemon=True).start()
    workers[channel_id].set_source(req.url)
    return {"ok": True}

@app.post("/api/channels/{channel_id}/stop", dependencies=[Depends(verify_token)])
async def stop_channel(channel_id: str):
    if channel_id not in workers:
        raise HTTPException(status_code=404, detail="Channel not found")
    workers[channel_id].stop_source()
    return {"ok": True}

@app.delete("/api/channels/{channel_id}/segments", dependencies=[Depends(verify_token)])
async def reset_segments(channel_id: str):
    if channel_id not in workers:
        raise HTTPException(status_code=404, detail="Channel not found")
    workers[channel_id].reset_window()
    return {"ok": True}

@app.delete("/api/channels/{channel_id}", dependencies=[Depends(verify_token)])
async def delete_channel(channel_id: str):
    if channel_id not in workers:
        raise HTTPException(status_code=404, detail="Channel not found")
    worker = workers[channel_id]
    worker.stop()
    # Clean up output directory
    output_dir = os.path.join(OUT_ROOT, "out", channel_id)
    if os.path.exists(output_dir):
        rmrf(output_dir)
    del workers[channel_id]
    return {"ok": True}
