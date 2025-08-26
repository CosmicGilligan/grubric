import anthropic
import os

def get_client():
    """Get Anthropic client instance"""
    return anthropic.Anthropic(
        api_key=get_key()
    )

def get_key():
    """Get your Anthropic API key"""
    # You'll need to add your Anthropic API key to your secrets file
    # or set it as an environment variable
    try:
        with open('/home/drkeithcox/canvas-secrets.key', 'r') as file:
            lines = [line.strip() for line in file]
        
        # Assuming you add the Anthropic key as the third line in your secrets file
        # Format: Canvas URL, Canvas Token, Anthropic API Key
        if len(lines) >= 3:
            return lines[2]  # Anthropic API key
        else:
            # Fallback to environment variable
            return os.getenv('ANTHROPIC_API_KEY')
    except FileNotFoundError:
        # Fallback to environment variable
        return os.getenv('ANTHROPIC_API_KEY')