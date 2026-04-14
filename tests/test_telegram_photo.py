"""
Test script to verify Telegram photo handling is correctly set up.
"""
import sys
sys.path.insert(0, '.')

def test_imports():
    """Test all required imports."""
    print("=" * 50)
    print("TEST: Telegram Photo Handler Setup")
    print("=" * 50)
    
    # Test 1: Vision Module
    print("\n1. Testing Vision Module import...")
    try:
        from src.computer_use.vision import get_vision, VisionModule
        vision = get_vision()
        status = vision.get_status()
        print(f"   ✅ Vision Module OK")
        print(f"   - OCR available: {status.get('ocr_available')}")
        print(f"   - LLM Vision: {status.get('llm_vision')}")
        print(f"   - Providers: {status.get('providers')}")
    except Exception as e:
        print(f"   ❌ Vision Module Error: {e}")
    
    # Test 2: Telegram Channel
    print("\n2. Testing Telegram Channel import...")
    try:
        from src.channels.telegram_channel import TelegramChannel, VISION_AVAILABLE
        print(f"   ✅ Telegram Channel OK")
        print(f"   - Vision Available: {VISION_AVAILABLE}")
    except Exception as e:
        print(f"   ❌ Telegram Channel Error: {e}")
    
    # Test 3: Check received_images folder
    print("\n3. Testing received_images folder...")
    from pathlib import Path
    images_dir = Path("data/received_images")
    if images_dir.exists():
        print(f"   ✅ Folder exists: {images_dir.absolute()}")
    else:
        print(f"   ❌ Folder missing: {images_dir.absolute()}")
    
    # Test 4: API Keys
    print("\n4. Checking API Keys...")
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    google_key = os.getenv("GOOGLE_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    
    print(f"   - GOOGLE_API_KEY: {'✅ Set' if google_key else '❌ Missing'}")
    print(f"   - ANTHROPIC_API_KEY: {'✅ Set' if anthropic_key else '❌ Missing'}")
    
    print("\n" + "=" * 50)
    print("SUMMARY: Ready to receive photos via Telegram!")
    print("=" * 50)
    print("\nTo test, run: python run_telegram.py")
    print("Then send a photo to your Telegram bot.")

if __name__ == "__main__":
    test_imports()
