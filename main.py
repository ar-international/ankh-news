import os
import requests
import imaplib
import email
from instagrapi import Client
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

# Instagram credentials
INSTAGRAM_USERNAME = os.getenv('INSTAGRAM_USERNAME')
INSTAGRAM_PASSWORD = os.getenv('INSTAGRAM_PASSWORD')

# Email credentials for fetching 2FA code
EMAIL_USER = os.getenv('EMAIL_USER')  # Your email address
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')  # Your email password
IMAP_SERVER = "imap.gmail.com"  # Change if using a different email provider

# The News API endpoint
NEWS_API_URL = "https://api.thenewsapi.com/v1/news/top"

# Base hashtags for the news channel
BASE_HASHTAGS = [
    "#BreakingNews", "#NewsUpdate", "#WorldNews", "#LatestNews", "#TopStories",
    "#GlobalNews", "#DailyNews", "#NewsAlert", "#CurrentEvents", "#NewsToday",
    "#Headlines", "#TrendingNews", "#NewsBreak", "#InTheNews", "#NewsFeed"
]

# File to store posted UUIDs
POSTED_UUIDS_FILE = "posted_uuids.txt"

# Maximum number of articles to process in one run
MAX_ARTICLES_TO_PROCESS = 10


def fetch_all_news():
    params = {
        'api_token': os.getenv('API_TOKEN'),  # Replace with your actual API token
        'locale': 'us',
    }
    response = requests.get(NEWS_API_URL, params=params)
    if response.status_code == 200:
        return response.json()['data']
    else:
        print("Failed to fetch news")
        return []


def download_image(image_url, filename):
    response = requests.get(image_url)
    if response.status_code == 200:
        with open(filename, 'wb') as file:
            file.write(response.content)
        return filename
    else:
        print("Failed to download image")
        return None


def post_to_instagram(image_path, caption):
    cl = Client()

    # Attempt to log in with 2FA
    try:
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    except Exception:
        # if "2FA" in str(e):
        print("2FA required. Fetching verification code from email...")
        verification_code = fetch_2fa_code_from_email()
        if verification_code:
            cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD, verification_code=verification_code)
            print("Logged in with 2FA code.")
        else:
            print("Failed to fetch 2FA code.")
            return
        #else:
        #    print(f"Login failed: {e}")
        #    return

    cl.photo_upload(image_path, caption)


def generate_hashtags(categories):
    # Convert categories to hashtags
    category_hashtags = [f"#{category}" for category in categories]
    # Combine base hashtags with category hashtags
    return BASE_HASHTAGS + category_hashtags


def load_posted_uuids():
    # Load UUIDs of already posted news items
    if os.path.exists(POSTED_UUIDS_FILE):
        with open(POSTED_UUIDS_FILE, 'r') as file:
            return set(file.read().splitlines())
    return set()


def save_posted_uuid(uuid):
    # Save the UUID of the posted news item
    with open(POSTED_UUIDS_FILE, 'a') as file:
        file.write(uuid + '\n')


def fetch_2fa_code_from_email():
    # Connect to the email server
    print(f"Login to: {EMAIL_USER}")
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASSWORD)
    mail.select("inbox")

    # Search for the latest email from Instagram
    status, messages = mail.search(None, '(FROM "security@mail.instagram.com")')
    if status != "OK":
        print("Failed to search emails.")
        return None

    # Get the latest email ID
    email_ids = messages[0].split()
    latest_email_id = email_ids[-1]

    # Fetch the email content
    status, msg_data = mail.fetch(latest_email_id, "(RFC822)")
    if status != "OK":
        print("Failed to fetch email.")
        return None

    # Parse the email content
    email_body = ''
    msg = email.message_from_bytes(msg_data[0][1])
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html":
                email_body = part.get_payload(decode=True).decode()
                break
    else:
        email_body = msg.get_payload(decode=True).decode()

    # Extract the 2FA code from the email body
    import re
    match = re.search(r"<font size=\"6\">(\d{6})</font>", email_body)
    if match:
        return match.group(1)
    else:
        print("2FA code not found in email.")
        return None


def main():
    # Load UUIDs of already posted news items
    posted_uuids = load_posted_uuids()

    # Fetch all news articles
    news_items = fetch_all_news()
    if not news_items:
        print("No news items found.")
        return

    # Process each news article
    for index, news_item in enumerate(news_items[:MAX_ARTICLES_TO_PROCESS]):
        uuid = news_item['uuid']

        # Check if the news item has already been posted
        if uuid in posted_uuids:
            print(f"News item with UUID {uuid} has already been posted. Skipping.")
            continue

        image_url = news_item['image_url']
        snippet = news_item['snippet']
        url = news_item['url']
        categories = news_item['categories']

        # Generate hashtags including categories
        hashtags = generate_hashtags(categories)
        hashtags_str = ' '.join(hashtags)

        # Create the caption with "Read more" link
        caption = f"{snippet}\n\nRead more: {url}\n\n{hashtags_str}"

        # Download the image
        image_filename = f"news_image_{datetime.now().strftime('%Y%m%d%H%M%S')}_{index}.jpg"
        image_path = download_image(image_url, image_filename)

        if image_path:
            # Post to Instagram
            post_to_instagram(image_path, caption)
            # Save the UUID of the posted news item
            save_posted_uuid(uuid)
            print(f"News item {index + 1} posted to Instagram successfully!")
        else:
            print(f"Failed to download image for news item {index + 1}")


if __name__ == "__main__":
    main()