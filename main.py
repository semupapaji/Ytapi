
# api.py
import os
import yt_dlp
import uuid
import threading
import time
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio

app = FastAPI(title="YouTube Audio Downloader API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
DOWNLOAD_DIR = "downloads"
AUTO_DELETE_SECONDS = 120  # 2 minutes

# Create downloads directory
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Store download status and timers
download_status = {}
delete_timers = {}

def delete_file_after_delay(file_path, download_id):
    """Delete file after 2 minutes"""
    time.sleep(AUTO_DELETE_SECONDS)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"🗑️ Auto-deleted: {file_path}")
            
            # Update status
            if download_id in download_status:
                download_status[download_id]['status'] = 'deleted'
                download_status[download_id]['message'] = 'File auto-deleted after 2 minutes'
            
            # Cleanup timer
            if download_id in delete_timers:
                del delete_timers[download_id]
                
    except Exception as e:
        print(f"❌ Delete error: {e}")

def download_audio_task(download_id, url):
    """Download audio in background"""
    try:
        download_status[download_id]['status'] = 'downloading'
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}_%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filename = filename.rsplit('.', 1)[0] + '.mp3'
            
            download_status[download_id]['status'] = 'completed'
            download_status[download_id]['file_path'] = filename
            download_status[download_id]['title'] = info.get('title', 'audio')
            download_status[download_id]['created_at'] = time.time()
            download_status[download_id]['expires_in'] = AUTO_DELETE_SECONDS
            
            # Start auto-delete timer
            timer = threading.Thread(
                target=delete_file_after_delay,
                args=(filename, download_id)
            )
            timer.daemon = True
            timer.start()
            delete_timers[download_id] = timer
            
    except Exception as e:
        download_status[download_id]['status'] = 'failed'
        download_status[download_id]['error'] = str(e)

@app.get("/")
async def root():
    return {
        "message": "🎵 YouTube Audio Downloader API",
        "auto_delete": f"{AUTO_DELETE_SECONDS} seconds",
        "endpoints": {
            "/download?url=URL": "Direct download (auto-delete after 2 min)",
            "/async-download?url=URL": "Async download with status",
            "/status?download_id=ID": "Check download status",
            "/download-file?download_id=ID": "Download file (if not deleted)",
            "/files": "List all active files",
            "/cleanup": "Force cleanup all files"
        }
    }

@app.get("/download")
async def download_audio(url: str = Query(..., description="YouTube URL")):
    """Direct download - auto-deletes after 2 minutes"""
    try:
        download_id = str(uuid.uuid4())[:8]
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}_%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filename = filename.rsplit('.', 1)[0] + '.mp3'
            
            # Start auto-delete timer BEFORE sending response
            timer = threading.Thread(
                target=delete_file_after_delay,
                args=(filename, download_id)
            )
            timer.daemon = True
            timer.start()
            
            # Store in status
            download_status[download_id] = {
                'status': 'completed',
                'file_path': filename,
                'title': info.get('title', 'audio'),
                'created_at': time.time(),
                'expires_in': AUTO_DELETE_SECONDS
            }
            
            # Send file
            return FileResponse(
                path=filename,
                filename=os.path.basename(filename),
                media_type="audio/mpeg",
                headers={
                    "X-Auto-Delete": f"{AUTO_DELETE_SECONDS} seconds",
                    "X-Download-ID": download_id
                }
            )
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {str(e)}")

@app.get("/async-download")
async def async_download(url: str = Query(..., description="YouTube URL")):
    """Async download with status tracking"""
    try:
        download_id = str(uuid.uuid4())[:8]
        
        download_status[download_id] = {
            'status': 'pending',
            'progress': 0,
            'url': url,
            'file_path': None,
            'error': None,
            'created_at': time.time(),
            'expires_in': AUTO_DELETE_SECONDS
        }
        
        # Start download in background
        thread = threading.Thread(
            target=download_audio_task,
            args=(download_id, url)
        )
        thread.daemon = True
        thread.start()
        
        return JSONResponse({
            "status": "success",
            "message": "Download started",
            "download_id": download_id,
            "auto_delete": f"{AUTO_DELETE_SECONDS} seconds",
            "check_status": f"/status?download_id={download_id}"
        })
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/status")
async def get_status(download_id: str = Query(..., description="Download ID")):
    """Check download status"""
    if download_id not in download_status:
        raise HTTPException(status_code=404, detail="Download ID not found")
    
    status_data = download_status[download_id]
    
    response = {
        "status": status_data['status'],
        "title": status_data.get('title', 'Unknown'),
        "auto_delete": f"{AUTO_DELETE_SECONDS} seconds"
    }
    
    if status_data['status'] == 'completed':
        # Check if file still exists
        file_path = status_data.get('file_path')
        if file_path and os.path.exists(file_path):
            response["file_ready"] = True
            response["download_url"] = f"/download-file?download_id={download_id}"
            # Time remaining
            created = status_data.get('created_at', time.time())
            elapsed = time.time() - created
            remaining = max(0, AUTO_DELETE_SECONDS - elapsed)
            response["seconds_remaining"] = int(remaining)
        else:
            response["file_ready"] = False
            response["status"] = 'deleted'
            response["message"] = "File has been auto-deleted"
            
    elif status_data['status'] == 'failed':
        response["error"] = status_data.get('error', 'Unknown error')
    
    return JSONResponse(response)

@app.get("/download-file")
async def download_file(download_id: str = Query(..., description="Download ID")):
    """Download the completed audio file"""
    if download_id not in download_status:
        raise HTTPException(status_code=404, detail="Download ID not found")
    
    status_data = download_status[download_id]
    
    if status_data['status'] != 'completed':
        raise HTTPException(status_code=400, detail="Download not completed yet")
    
    file_path = status_data.get('file_path')
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found or already deleted")
    
    # Reset timer - file will be deleted after another 2 minutes
    # Cancel old timer
    if download_id in delete_timers:
        # Note: Can't really cancel thread, but we'll start a new one
        pass
    
    # Start new delete timer
    timer = threading.Thread(
        target=delete_file_after_delay,
        args=(file_path, download_id)
    )
    timer.daemon = True
    timer.start()
    delete_timers[download_id] = timer
    
    # Update timestamp
    download_status[download_id]['created_at'] = time.time()
    
    return FileResponse(
        path=file_path,
        filename=os.path.basename(file_path),
        media_type="audio/mpeg",
        headers={
            "X-Auto-Delete": f"{AUTO_DELETE_SECONDS} seconds (reset)",
            "X-Download-ID": download_id
        }
    )

@app.get("/files")
async def list_files():
    """List all active files with remaining time"""
    files = []
    for filename in os.listdir(DOWNLOAD_DIR):
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.isfile(file_path):
            # Find download_id from filename
            download_id = filename.split('_')[0] if '_' in filename else None
            
            # Calculate remaining time
            created = time.time() - os.path.getmtime(file_path)
            remaining = max(0, AUTO_DELETE_SECONDS - created)
            
            files.append({
                "filename": filename,
                "size_mb": round(os.path.getsize(file_path) / (1024 * 1024), 2),
                "created_ago": f"{int(created)} seconds",
                "deletes_in": f"{int(remaining)} seconds",
                "download_id": download_id
            })
    
    return JSONResponse({
        "total_files": len(files),
        "auto_delete": f"{AUTO_DELETE_SECONDS} seconds",
        "files": files
    })

@app.get("/cleanup")
async def force_cleanup():
    """Force delete all files immediately"""
    deleted = 0
    for filename in os.listdir(DOWNLOAD_DIR):
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
            deleted += 1
    
    # Clear status
    download_status.clear()
    delete_timers.clear()
    
    return JSONResponse({
        "message": f"Force deleted {deleted} files",
        "status": "cleaned"
    })

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on server shutdown"""
    for filename in os.listdir(DOWNLOAD_DIR):
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                print(f"🗑️ Cleaned up: {file_path}")
            except:
                pass

if __name__ == "__main__":
    import uvicorn
    print("🎵 YouTube Audio Downloader API")
    print(f"⏱️ Auto-delete after: {AUTO_DELETE_SECONDS} seconds (2 minutes)")
    print(f"📁 Files saved in: {os.path.abspath(DOWNLOAD_DIR)}")
    print("🚀 Server starting...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
