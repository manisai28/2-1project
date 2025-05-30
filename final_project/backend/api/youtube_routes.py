from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from fastapi.responses import RedirectResponse
import os
import requests
from urllib.parse import urlencode
from datetime import datetime
from bson import ObjectId
import google.oauth2.credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from models.user import UserResponse
from utils.auth import get_current_user
from config.db import get_db

# Create router
youtube_router = APIRouter()

# YouTube OAuth2 configuration
CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:3000/dashboard"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")

@youtube_router.get("/auth")
async def youtube_auth(current_user: dict = Depends(get_current_user)):
    """Initiate YouTube OAuth2 flow"""
    # Construct the authorization URL
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "state": str(current_user["_id"]),  # Pass user ID as state for security
        "prompt": "consent",
        "include_granted_scopes": "true"
    }
    
    auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(auth_params)}"
    return {"auth_url": auth_url}

@youtube_router.get("/callback")
async def youtube_callback(code: str = None, state: str = None, error: str = None, current_user: dict = Depends(get_current_user)):
    """Handle OAuth2 callback from Google"""
    if error:
        print(f"OAuth error: {error}")
        return {"success": False, "error": error}
        
    if not code or not state:
        return {"success": False, "error": "Missing code or state parameter"}
    
    # Verify state matches current user ID for security
    if str(current_user["_id"]) != state:
        return {"success": False, "error": "Invalid state parameter"}
    
    db = get_db()
    
    try:
        # Exchange authorization code for tokens
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code"
        }
        
        token_response = requests.post(token_url, data=token_data)
        token_response.raise_for_status()
        token_info = token_response.json()
        
        # Store tokens in database
        user_id = state  # This is the user ID we passed as state
        
        # Update user with YouTube tokens
        db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "youtube_access_token": token_info["access_token"],
                "youtube_refresh_token": token_info.get("refresh_token"),
                "youtube_token_expiry": datetime.now().timestamp() + token_info["expires_in"],
                "youtube_connected": True,
                "updated_at": datetime.now()
            }}
        )
        
        # Verify the connection by making a test API call
        headers = {
            "Authorization": f"Bearer {token_info['access_token']}",
            "Accept": "application/json"
        }
        
        # Get channel info to verify connection
        channel_response = requests.get(
            "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
            headers=headers
        )
        
        if channel_response.status_code == 200:
            channel_data = channel_response.json()
            if channel_data.get("items") and len(channel_data["items"]) > 0:
                channel_title = channel_data["items"][0]["snippet"]["title"]
                print(f"Successfully connected to YouTube channel: {channel_title}")
                
                # Store channel info
                db.users.update_one(
                    {"_id": ObjectId(user_id)},
                    {"$set": {
                        "youtube_channel_title": channel_title,
                        "youtube_channel_id": channel_data["items"][0]["id"]
                    }}
                )
                
                return {
                    "success": True, 
                    "message": f"Connected to YouTube channel: {channel_title}"
                }
        
        return {"success": True, "message": "YouTube account connected successfully"}
        
    except Exception as e:
        print(f"OAuth callback error: {str(e)}")
        return {"success": False, "error": str(e)}

@youtube_router.post("/upload/{video_id}")
async def upload_to_youtube(
    video_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Upload a processed video to YouTube with SEO tags"""
    try:
        # Parse request body
        try:
            body = await request.json()
            title = body.get("title")
            description = body.get("description")
            tags = body.get("tags")
            privacy_status = body.get("privacy_status", "private")
        except Exception as e:
            print(f"Error parsing request body: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request body: {str(e)}"
            )
        
        db = get_db()
        
        # Check if user has YouTube tokens
        if not current_user.get("youtube_connected"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="YouTube account not connected"
            )
        
        # Find video by ID
        try:
            video = db.videos.find_one({"_id": ObjectId(video_id), "user_id": str(current_user["_id"])})
            if not video:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Video not found"
                )
        except Exception as e:
            print(f"Error finding video: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error retrieving video: {str(e)}"
            )
        
        # Check if access token is expired and refresh if needed
        if current_user.get("youtube_token_expiry", 0) < datetime.now().timestamp():
            try:
                # Refresh token
                refresh_response = requests.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": CLIENT_ID,
                        "client_secret": CLIENT_SECRET,
                        "refresh_token": current_user["youtube_refresh_token"],
                        "grant_type": "refresh_token"
                    }
                )
                refresh_response.raise_for_status()
                refresh_data = refresh_response.json()
                
                # Update tokens in database
                db.users.update_one(
                    {"_id": current_user["_id"]},
                    {"$set": {
                        "youtube_access_token": refresh_data["access_token"],
                        "youtube_token_expiry": datetime.now().timestamp() + refresh_data["expires_in"],
                        "updated_at": datetime.now()
                    }}
                )
                
                # Update current user object
                current_user["youtube_access_token"] = refresh_data["access_token"]
            except Exception as e:
                print(f"Error refreshing token: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Failed to refresh YouTube token: {str(e)}"
                )
                
        # Get video keywords if available
        keywords = []
        try:
            if video.get("keywords_id"):
                keyword_doc = db.keywords.find_one({"_id": ObjectId(video["keywords_id"])})
                if keyword_doc and keyword_doc.get("keywords") and len(keyword_doc["keywords"]) > 0:
                    # Handle different keyword formats
                    if isinstance(keyword_doc["keywords"][0], dict) and "keyword" in keyword_doc["keywords"][0]:
                        keywords = [k["keyword"] for k in keyword_doc["keywords"]]
                    elif isinstance(keyword_doc["keywords"][0], str):
                        keywords = keyword_doc["keywords"]
        except Exception as e:
            print(f"Error retrieving keywords: {str(e)}")
            keywords = []

        # Use provided title or fallback to video title
        video_title = title or video.get("title", "Untitled Video")
        
        # Use provided description or generate one
        keyword_text = ", ".join(keywords[:5]) if keywords else "SEO optimized content"
        video_description = description or f"Video analyzed with SEO keywords: {keyword_text}"
        
        # Use provided tags or use keywords
        video_tags = tags or keywords
        
        # Get the video file path
        video_path = os.path.join(UPLOAD_DIR, video["filename"])
        if not os.path.exists(video_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Video file not found"
            )

        # Create YouTube API client
        credentials = google.oauth2.credentials.Credentials(
            token=current_user["youtube_access_token"],
            refresh_token=current_user["youtube_refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )

        youtube = build('youtube', 'v3', credentials=credentials)

        # Create video metadata
        body = {
            'snippet': {
                'title': video_title,
                'description': video_description,
                'tags': video_tags[:30] if video_tags else [],  # YouTube allows max 30 tags
                'categoryId': '22'  # Category ID for People & Blogs
            },
            'status': {
                'privacyStatus': privacy_status,
                'selfDeclaredMadeForKids': False
            }
        }

        # Upload the video
        try:
            # Insert the video
            insert_request = youtube.videos().insert(
                part=','.join(body.keys()),
                body=body,
                media_body=MediaFileUpload(
                    video_path,
                    chunksize=-1,
                    resumable=True
                )
            )

            # Execute the upload
            response = insert_request.execute()
            youtube_video_id = response['id']

            # Update video with YouTube info
            db.videos.update_one(
                {"_id": ObjectId(video_id)},
                {"$set": {
                    "youtube_uploaded": True,
                    "youtube_video_id": youtube_video_id,
                    "youtube_upload_date": datetime.now(),
                    "youtube_title": video_title,
                    "youtube_description": video_description,
                    "youtube_tags": video_tags[:30] if video_tags else [],
                    "updated_at": datetime.now()
                }}
            )

            return {
                "success": True,
                "message": "Video uploaded to YouTube successfully",
                "video_id": video_id,
                "youtube_video_id": youtube_video_id,
                "youtube_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
                "youtube_title": video_title,
                "youtube_description": video_description,
                "youtube_tags": video_tags[:30] if video_tags else []
            }

        except HttpError as e:
            error_content = e.content.decode('utf-8')
            print(f"An HTTP error occurred: {error_content}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"YouTube upload failed: {error_content}"
            )

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error uploading to YouTube: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload to YouTube: {str(e)}"
        )
        
        # Get video keywords if available
        keywords = []
        try:
            if video.get("keywords_id"):
                keyword_doc = db.keywords.find_one({"_id": ObjectId(video["keywords_id"])})
                if keyword_doc and keyword_doc.get("keywords") and len(keyword_doc["keywords"]) > 0:
                    # Handle different keyword formats
                    if isinstance(keyword_doc["keywords"][0], dict) and "keyword" in keyword_doc["keywords"][0]:
                        # Format: [{"keyword": "value", ...}, ...]
                        keywords = [k["keyword"] for k in keyword_doc["keywords"]]
                    elif isinstance(keyword_doc["keywords"][0], str):
                        # Format: ["keyword1", "keyword2", ...]
                        keywords = keyword_doc["keywords"]
                    else:
                        # Unknown format, log for debugging
                        print(f"Unknown keyword format: {type(keyword_doc['keywords'][0])}")
                        print(f"Sample: {keyword_doc['keywords'][0]}")
                        # Use empty list as fallback
                        keywords = []
        except Exception as e:
            print(f"Error retrieving keywords: {str(e)}")
            # Continue with empty keywords list
            keywords = []
        
        # Use provided title or fallback to video title
        video_title = title or video.get("title", "Untitled Video")
        
        # Use provided description or generate one
        keyword_text = ", ".join(keywords[:5]) if keywords else "SEO optimized content"
        video_description = description or f"Video analyzed with SEO keywords: {keyword_text}"
        
        # Use provided tags or use keywords
        video_tags = tags or keywords
        
        # Get the video file path
        try:
            video_path = os.path.join(UPLOAD_DIR, video["filename"])
            if not os.path.exists(video_path):
                print(f"Video file not found at path: {video_path}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Video file not found"
                )
        except Exception as e:
            print(f"Error accessing video file: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error accessing video file: {str(e)}"
            )
        
        # For now, we'll simulate a successful upload since the actual implementation
        # requires a more complex setup with the YouTube API client library
        # In a production environment, you would use the Google API Python client library
        
        # Simulate successful upload
        youtube_video_id = f"simulated-yt-{video_id[:8]}"
        
        # Update video with YouTube info
        try:
            db.videos.update_one(
                {"_id": ObjectId(video_id)},
                {"$set": {
                    "youtube_uploaded": True,
                    "youtube_video_id": youtube_video_id,
                    "youtube_upload_date": datetime.now(),
                    "updated_at": datetime.now()
                }}
            )
        except Exception as e:
            print(f"Error updating video record: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating video record: {str(e)}"
            )
        
        return {
            "success": True,
            "message": "Video uploaded to YouTube successfully",
            "video_id": video_id,
            "youtube_video_id": youtube_video_id,
            "youtube_title": video_title,
            "youtube_tags": video_tags[:5] if video_tags else []  # Return first 5 tags for display
        }
    
    except HTTPException as e:
        # Re-raise HTTP exceptions
        raise e
    except Exception as e:
        print(f"Error uploading to YouTube: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload to YouTube: {str(e)}"
        )

@youtube_router.post("/upload-url/{video_id}")
async def get_youtube_upload_url(
    video_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get a URL to redirect users to YouTube for uploading a video"""
    try:
        # Parse request body
        try:
            body = await request.json()
            title = body.get("title", "Untitled Video")
            description = body.get("description", "")
            tags = body.get("tags", [])
        except Exception as e:
            print(f"Error parsing request body: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request body: {str(e)}"
            )
        
        db = get_db()
        
        # Check if user has YouTube tokens
        if not current_user.get("youtube_connected"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="YouTube account not connected"
            )
        
        # Find video by ID
        try:
            video = db.videos.find_one({"_id": ObjectId(video_id), "user_id": str(current_user["_id"])})
            if not video:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Video not found"
                )
        except Exception as e:
            print(f"Error finding video: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error retrieving video: {str(e)}"
            )
        
        # Check if access token is expired and refresh if needed
        if current_user.get("youtube_token_expiry", 0) < datetime.now().timestamp():
            try:
                # Refresh token
                refresh_response = requests.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": CLIENT_ID,
                        "client_secret": CLIENT_SECRET,
                        "refresh_token": current_user["youtube_refresh_token"],
                        "grant_type": "refresh_token"
                    }
                )
                refresh_response.raise_for_status()
                refresh_data = refresh_response.json()
                
                # Update tokens in database
                db.users.update_one(
                    {"_id": current_user["_id"]},
                    {"$set": {
                        "youtube_access_token": refresh_data["access_token"],
                        "youtube_token_expiry": datetime.now().timestamp() + refresh_data["expires_in"],
                        "updated_at": datetime.now()
                    }}
                )
                
                # Update current user object
                current_user["youtube_access_token"] = refresh_data["access_token"]
            except Exception as e:
                print(f"Error refreshing token: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Failed to refresh YouTube token: {str(e)}"
                )
        
        # YouTube doesn't have a direct public URL for pre-filled uploads
        # We'll use the standard YouTube upload page and provide the metadata in the response
        # The frontend will need to open this URL and then guide the user to manually enter the metadata
        redirect_url = "https://www.youtube.com/upload"
        
        # Update video with YouTube info
        try:
            db.videos.update_one(
                {"_id": ObjectId(video_id)},
                {"$set": {
                    "youtube_redirect_attempted": True,
                    "youtube_redirect_date": datetime.now(),
                    "updated_at": datetime.now()
                }}
            )
        except Exception as e:
            print(f"Error updating video record: {str(e)}")
            # Continue anyway since this is not critical
        
        # Return the redirect URL and metadata for the frontend to display
        return {
            "success": True,
            "message": "YouTube upload URL generated successfully",
            "redirectUrl": redirect_url,
            "video_id": video_id,
            "youtube_title": title,
            "youtube_description": description,
            "youtube_tags": tags[:30] if tags else []  # YouTube allows up to 30 tags
        }
    
    except HTTPException as e:
        # Re-raise HTTP exceptions
        raise e
    except Exception as e:
        print(f"Error generating YouTube upload URL: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate YouTube upload URL: {str(e)}"
        )

@youtube_router.get("/status")
async def youtube_status(current_user: dict = Depends(get_current_user)):
    """Check if user has connected YouTube account"""
    try:
        connected = current_user.get("youtube_connected", False)
        channel_title = current_user.get("youtube_channel_title", "")
        
        # Check if token is expired and needs refresh
        if connected and current_user.get("youtube_token_expiry"):
            expiry_time = current_user["youtube_token_expiry"]
            if datetime.now().timestamp() > expiry_time:
                # Token expired, try to refresh
                if current_user.get("youtube_refresh_token"):
                    try:
                        new_token = await refresh_youtube_token(current_user)
                        if not new_token:
                            connected = False
                    except:
                        connected = False
        
        return {
            "connected": connected,
            "channel_title": channel_title if connected else ""
        }
    except Exception as e:
        print(f"Error checking YouTube status: {str(e)}")
        return {"connected": False, "error": str(e)}

async def refresh_youtube_token(user):
    """Refresh YouTube access token"""
    try:
        refresh_token = user.get("youtube_refresh_token")
        if not refresh_token:
            return None
            
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        
        token_response = requests.post(token_url, data=token_data)
        token_response.raise_for_status()
        token_info = token_response.json()
        
        # Update user with new token
        db = get_db()
        db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {
                "youtube_access_token": token_info["access_token"],
                "youtube_token_expiry": datetime.now().timestamp() + token_info["expires_in"],
                "updated_at": datetime.now()
            }}
        )
        
        return token_info["access_token"]
    except Exception as e:
        print(f"Error refreshing YouTube token: {str(e)}")
        return None
