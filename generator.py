import os
import urllib.parse
import google.generativeai as genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def generate_watermark_url(original_url: str, price: int, title: str) -> str:
    """Uses Cloudinary fetch to overlay price on the image automatically."""
    encoded_price = urllib.parse.quote_plus(f"{price} QAR")
    prefix = "https://res.cloudinary.com/dskhwgcrx/image/fetch/"
    
    # We use a white background tag with the price, bottom left.
    transform = f"w_1080,c_fit/co_white,bg_red,l_text:helvetica_80_bold:{encoded_price}/fl_layer_apply,g_south_west,y_50,x_50/"
    return f"{prefix}{transform}{original_url}"

def generate_caption(title: str, price: int, year: int, mileage: int, url: str) -> str:
    if not GEMINI_API_KEY:
        return f"🚨 DEAL ALERT! 🚨\n{title}\n\nPrice: {price} QAR\nLink: {url}\n\n#QatarCars #DohaDeals"

    prompt = f"""
    Write a viral, high-energy Instagram/Facebook caption for this car deal in Qatar.
    It must be in both English and Arabic.
    Include emojis, hashtags, and a clear call to action to message us for the link.
    
    Car: {title}
    Price: {price} QAR
    Year: {year}
    Mileage: {mileage} km
    Link: {url}
    
    Make it sound extremely urgent and like a rare find (because it is!).
    """
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Gemini error: {e}")
        return f"🚨 DEAL ALERT! 🚨\n{title}\n\nPrice: {price} QAR\nLink: {url}\n\n#QatarCars #DohaDeals"
