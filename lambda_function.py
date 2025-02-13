import os
import requests
import imaplib
import email
import boto3
import json
import random
import nltk
from datetime import timedelta
from instagrapi import Client
from dotenv import load_dotenv
from datetime import datetime
from tempfile import gettempdir
from newspaper import Article

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

MAX_ARTICLES_TO_PROCESS = random.randint(1, 3)

s3 = boto3.client('s3')
BUCKET_NAME = os.getenv('BUCKET_NAME')

nltk_data_path = os.path.join(os.path.dirname(__file__), 'nltk_data')
nltk.data.path.append(nltk_data_path)
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', download_dir=nltk_data_path)


def get_article_content(url, timeout=10):
    """Fetch and parse article content using newspaper3k"""
    try:
        article = Article(
            url,
            request_timeout=timeout,
            browser_user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        )
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        print(f"Error fetching article content from {url}: {str(e)}")
        return None


def generate_snippet(text, max_length=280):
    """Generate a clean snippet ending with complete sentences"""
    if not text:
        return ""

    # Use NLTK for accurate sentence tokenization
    sentences = nltk.sent_tokenize(text)
    snippet = []
    total_length = 0

    for sentence in sentences:
        if total_length + len(sentence) + 1 <= max_length:  # +1 for space
            snippet.append(sentence)
            total_length += len(sentence) + 1
        else:
            break

    # Fallback if first sentence is too long
    if not snippet and len(text) > max_length:
        return text[:max_length].strip() + "..."

    return ' '.join(snippet).strip()


def clean_original_snippet(snippet):
    """Clean the original snippet from unwanted phrases"""
    unwanted_phrases = [
        'Already have an account?',
        'Log in here',
        'Sign up',
        'Click here',
        'Subscribe'
    ]
    lines = [line for line in snippet.split('\n')
             if not any(phrase in line for phrase in unwanted_phrases)]
    return ' '.join(lines).strip()


def load_posted_uuids():
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key='posted_uuids.txt')
        return set(obj['Body'].read().decode('utf-8').splitlines())
    except s3.exceptions.NoSuchKey:
        return set()


def save_posted_uuid(uuid):
    posted = load_posted_uuids()
    posted.add(uuid)
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key='posted_uuids.txt',
        Body='\n'.join(posted)
    )


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


def download_image(image_url, uuid):
    """Downloads image to Lambda's /tmp directory and uploads to S3"""
    try:
        # Create temporary filename using UUID
        filename = f"{uuid}.jpg"
        temp_path = os.path.join(gettempdir(), filename)

        # Download image
        response = requests.get(image_url, stream=True)
        response.raise_for_status()

        # Save to temporary directory
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Upload to S3 for persistent storage
        s3.upload_file(
            Filename=temp_path,
            Bucket=BUCKET_NAME,
            Key=filename
        )

        return temp_path

    except Exception as e:
        print(f"Image download/upload failed: {str(e)}")
        return None


def cleanup_temp_files(temp_path):
    """Clean up temporary files after posting"""
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception as e:
        print(f"Error cleaning up files: {str(e)}")


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

    cl.photo_upload(image_path, caption)


def generate_hashtags(categories):
    # Convert categories to hashtags
    category_hashtags = [f"#{category}" for category in categories]
    # Combine base hashtags with category hashtags
    return BASE_HASHTAGS + category_hashtags


def fetch_2fa_code_from_email():
    # Connect to the email server
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


def update_schedule(event, context):

    events = boto3.client('events')

    # Generate random hours between 1 and 6
    next_hours = random.uniform(1, 6)

    # Calculate the next run time
    next_run = datetime.now() + timedelta(hours=next_hours)

    cron = f"cron({next_run.minute} {next_run.hour} {next_run.day} {next_run.month} ? {next_run.year})"

    try:
        events.put_rule(
            Name='random-poster',
            ScheduleExpression=cron,
            State='ENABLED'
        )

        print(f"Schedule updated. Next run in {next_hours:.2f} hours at {next_run}")
        return {
            'statusCode': 200,
            'body': f"Schedule updated successfully. Next run at {next_run}"
        }
    except Exception as e:
        print(f"Error updating schedule: {str(e)}")
        raise e


def lambda_handler(event, context):
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

        # Download image to /tmp and S3
        temp_image_path = download_image(image_url, uuid)
        if not temp_image_path:
            continue

        try:
            # Post to Instagram using temporary file
            post_to_instagram(temp_image_path, caption)
            save_posted_uuid(uuid)
            update_schedule(event, context)

        finally:
            # Always clean up temporary files
            cleanup_temp_files(temp_image_path)

    return {
        'statusCode': 200,
        'body': json.dumps('News posted successfully!')
    }
