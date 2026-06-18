from dotenv import load_dotenv
import os

load_dotenv()

token = os.getenv("HUGGINGFACE_API_KEY")

print("TOKEN FOUND:", token is not None)

if token:
    print(token[:10] + "...")