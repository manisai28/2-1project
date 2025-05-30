from fastapi import APIRouter, Depends, HTTPException, status, Request
from bson.objectid import ObjectId
from datetime import datetime
import logging
import os
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create router
user_router = APIRouter()

# Load environment variables
load_dotenv()

# Database reference - will be set by the app
db = None

def set_db(database):
    """Set the database reference"""
    global db
    db = database
    logger.info("Database reference set in user_routes_simple")

async def get_current_user(request: Request):
    """Simple mock function for getting the current user"""
    # In a real app, this would verify the JWT token
    # For now, we'll just return a mock user
    return {
        "_id": "60d21b4667d0d8992e610c85",
        "username": "testuser",
        "email": "test@example.com",
        "phone_number": "+919876543210",
        "notification_preferences": {
            "subscribers": True,
            "likes": True,
            "shares": True,
            "thresholds": {
                "subscribers": 100,
                "likes": 50,
                "shares": 25
            }
        }
    }

@user_router.get("/profile")
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    """Get the user's profile"""
    logger.info(f"Getting profile for user: {current_user.get('_id')}")
    
    try:
        if db:
            user = db.users.find_one({"_id": ObjectId(current_user.get("_id"))})
            if user:
                user["_id"] = str(user["_id"])
                return user
        return current_user
    except Exception as e:
        logger.error(f"Error getting user profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user profile"
        )

@user_router.put("/profile")
async def update_user_profile(request: Request, current_user: dict = Depends(get_current_user)):
    """Update the user's profile"""
    try:
        data = await request.json()
        logger.info(f"Updating profile for user: {current_user.get('_id')}")
        
        if db:
            result = db.users.update_one(
                {"_id": ObjectId(current_user.get("_id"))},
                {"$set": {
                    "phone_number": data.get("phone_number"),
                    "updated_at": datetime.utcnow()
                }}
            )
            if result.modified_count > 0:
                return {"message": "Profile updated successfully"}
            else:
                user_data = {
                    "_id": ObjectId(current_user.get("_id")),
                    "username": current_user.get("username"),
                    "email": current_user.get("email"),
                    "phone_number": data.get("phone_number"),
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
                db.users.insert_one(user_data)
                return {"message": "Profile created successfully"}
        return {"message": "Profile updated successfully (mock)"}
    except Exception as e:
        logger.error(f"Error updating user profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user profile"
        )

@user_router.put("/notification-preferences")
async def update_notification_preferences(request: Request, current_user: dict = Depends(get_current_user)):
    """Update the user's notification preferences"""
    try:
        data = await request.json()
        logger.info(f"Updating notification preferences for user: {current_user.get('_id')}")
        
        if db:
            result = db.users.update_one(
                {"_id": ObjectId(current_user.get("_id"))},
                {"$set": {
                    "notification_preferences": data,
                    "updated_at": datetime.utcnow()
                }}
            )
            if result.modified_count > 0:
                return {"message": "Notification preferences updated successfully"}
            else:
                user_data = {
                    "_id": ObjectId(current_user.get("_id")),
                    "username": current_user.get("username"),
                    "email": current_user.get("email"),
                    "notification_preferences": data,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
                db.users.insert_one(user_data)
                return {"message": "Notification preferences created successfully"}
        return {"message": "Notification preferences updated successfully (mock)"}
    except Exception as e:
        logger.error(f"Error updating notification preferences: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update notification preferences"
        )

@user_router.get("/notifications")
async def get_notifications(limit: int = 10, skip: int = 0, current_user: dict = Depends(get_current_user)):
    """Get the user's notifications"""
    logger.info(f"Getting notifications for user: {current_user.get('_id')}")
    
    try:
        if db:
            notifications = list(db.notifications.find(
                {"user_id": current_user.get("_id")},
                {"_id": 0}
            ).sort("created_at", -1).skip(skip).limit(limit))
            return notifications
        
        return [
            {
                "type": "subscribers",
                "message": "Your video has reached 100 subscribers!",
                "created_at": datetime.utcnow().isoformat()
            },
            {
                "type": "likes",
                "message": "Your video has reached 50 likes!",
                "created_at": datetime.utcnow().isoformat()
            }
        ]
    except Exception as e:
        logger.error(f"Error getting notifications: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get notifications"
        )
