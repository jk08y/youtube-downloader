import logging
import os
import re
from datetime import datetime
from pathlib import Path
from enum import Enum
from typing import Dict, List

from flask import Flask, jsonify, render_template, request, send_file
from pytubefix import YouTube
from werkzeug.utils import secure_filename

class DownloadType(Enum):
    AUDIO = 'audio'
    VIDEO = 'video'

class Quality(Enum):
    AUDIO_LOW = 'low'
    AUDIO_MEDIUM = 'medium'
    AUDIO_HIGH = 'high'
    VIDEO_360P = '360p'
    VIDEO_480P = '480p'
    VIDEO_720P = '720p'
    VIDEO_1080P = '1080p'
    VIDEO_1440P = '1440p'
    VIDEO_2160P = '2160p'

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64MB max-limit
app.config['DOWNLOAD_FOLDER'] = Path('downloads')
app.config['DOWNLOAD_FOLDER'].mkdir(exist_ok=True)

# Enhanced logging with file handler
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('youtube_downloader.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def validate_youtube_url(url):
    """Validate YouTube URL format with improved regex."""
    youtube_regex = (
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    match = re.match(youtube_regex, url)
    if not match:
        raise ValueError("Invalid YouTube URL format")
    return url

def sanitize_filename(filename, file_type):
    """Sanitize filename for safe storage with additional characters removal."""
    # Remove any potentially problematic characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = secure_filename(filename)
    filename = re.sub(r'\s+', '_', filename)
    
    # Add timestamp and proper extension
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base, ext = os.path.splitext(filename)
    if not ext:
        ext = '.mp3' if file_type == DownloadType.AUDIO else '.mp4'
    
    # Truncate filename to prevent OS filename length limits
    return f"{base[:100]}_{timestamp}{ext}"[:255]

def get_video_info(url):
    """Get comprehensive video information including all available formats."""
    try:
        yt = YouTube(url)
        streams_info = {
            'audio': [],
            'video': []
        }
        
        # Get audio streams and sort by quality
        audio_streams = yt.streams.filter(only_audio=True).order_by('abr').desc()
        for stream in audio_streams:
            bitrate = stream.abr
            if not bitrate:
                continue
                
            # Improved audio quality classification
            quality_label = (
                'High' if 'kbps' in bitrate and int(bitrate.replace('kbps', '')) >= 128 
                else 'Medium' if 'kbps' in bitrate and int(bitrate.replace('kbps', '')) >= 64 
                else 'Low'
            )
            streams_info['audio'].append({
                'itag': stream.itag,
                'type': 'audio',
                'format': 'mp3',
                'quality': quality_label,
                'bitrate': bitrate,
                'size': f"{stream.filesize / (1024*1024):.1f} MB"
            })
        
        # Get all available video streams
        video_streams = yt.streams.filter(progressive=True).order_by('resolution').desc()
        adaptive_streams = yt.streams.filter(adaptive=True, type='video').order_by('resolution').desc()
        
        # Process and deduplicate video streams with more detailed info
        processed_resolutions = set()
        for stream in list(video_streams) + list(adaptive_streams):
            resolution = stream.resolution
            if not resolution or resolution in processed_resolutions:
                continue
                
            processed_resolutions.add(resolution)
            streams_info['video'].append({
                'itag': stream.itag,
                'type': 'video',
                'format': 'mp4',
                'quality': resolution,
                'fps': stream.fps,
                'mime_type': stream.mime_type,
                'size': f"{stream.filesize / (1024*1024):.1f} MB"
            })
        
        # Sort video streams by resolution (numerical value)
        streams_info['video'].sort(
            key=lambda x: int(x['quality'].replace('p', '')),
            reverse=True
        )
        
        return {
            'title': yt.title,
            'author': yt.author,
            'length': yt.length,
            'thumbnail_url': yt.thumbnail_url,
            'description': yt.description[:300] + '...' if yt.description else '',
            'view_count': yt.views,
            'publish_date': yt.publish_date.strftime("%Y-%m-%d") if yt.publish_date else None,
            'streams': streams_info
        }
    except Exception as e:
        logger.error(f"Error getting video info: {str(e)}")
        raise

def download_content(url, itag, download_type):
    """Download video or audio content with specified quality and error handling."""
    try:
        yt = YouTube(url)
        stream = yt.streams.get_by_itag(itag)
        
        if not stream:
            raise ValueError("Selected format is not available")
        
        # Improved file naming and path handling
        file_extension = '.mp3' if download_type == DownloadType.AUDIO else '.mp4'
        quality_suffix = f"_{stream.resolution}" if stream.resolution else ""
        filename = sanitize_filename(f"{yt.title}{quality_suffix}{file_extension}", download_type)
        file_path = app.config['DOWNLOAD_FOLDER'] / filename
        
        # Add a download progress callback (optional)
        def on_progress(stream, chunk, bytes_remaining):
            total_size = stream.filesize
            bytes_downloaded = total_size - bytes_remaining
            percentage_of_completion = bytes_downloaded / total_size * 100
            logger.info(f"Download progress: {percentage_of_completion:.2f}%")
        
        yt.register_on_progress_callback(on_progress)
        
        # Download with error handling and logging
        stream.download(
            output_path=str(app.config['DOWNLOAD_FOLDER']),
            filename=filename
        )
        
        logger.info(f"Successfully downloaded: {filename}")
        return str(file_path)
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise

# Routes remain largely the same with minor enhancements
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/video-info', methods=['POST'])
def get_info():
    try:
        url = request.json.get('url')
        validate_youtube_url(url)
        video_info = get_video_info(url)
        return jsonify({'success': True, 'data': video_info})
    except ValueError as ve:
        logger.warning(f"Invalid URL attempt: {ve}")
        return jsonify({'success': False, 'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"Unexpected error in video info retrieval: {e}")
        return jsonify({'success': False, 'error': 'An unexpected error occurred'}), 500

@app.route('/api/download', methods=['POST'])
def download():
    try:
        url = request.json.get('url')
        itag = request.json.get('itag')
        download_type = DownloadType(request.json.get('type', 'audio'))
        
        validate_youtube_url(url)
        file_path = download_content(url, itag, download_type)
        
        return jsonify({
            'success': True,
            'download_url': f'/download/{os.path.basename(file_path)}'
        })
    except ValueError as ve:
        logger.warning(f"Download error: {ve}")
        return jsonify({'success': False, 'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        return jsonify({'success': False, 'error': 'An unexpected error occurred during download'}), 500

@app.route('/download/<filename>')
def serve_file(filename):
    try:
        return send_file(
            app.config['DOWNLOAD_FOLDER'] / filename,
            as_attachment=True,
            download_name=filename
        )
    except FileNotFoundError:
        logger.warning(f"File not found: {filename}")
        return jsonify({'success': False, 'error': 'File not found'}), 404
    except Exception as e:
        logger.error(f"File serving error: {str(e)}")
        return jsonify({'success': False, 'error': 'An unexpected error occurred'}), 500

@app.errorhandler(413)
def too_large(e):
    logger.warning("File upload exceeded size limit")
    return jsonify({'success': False, 'error': 'File too large'}), 413

@app.errorhandler(404)
def not_found(e):
    logger.warning(f"404 error: {request.url}")
    return render_template('error.html', error_message="Page not found"), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"500 error: {str(e)}")
    return render_template('error.html', error_message="Internal server error"), 500

if __name__ == '__main__':
    # Added some startup logging
    logger.info("YouTube Downloader Application Starting...")
    try:
        app.run(debug=True)
    except Exception as e:
        logger.critical(f"Failed to start application: {e}")
