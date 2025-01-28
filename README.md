# Telegram-Anthropic-Voice

A sophisticated Telegram bot that integrates Claude AI with voice capabilities, allowing users to interact through text or voice messages while maintaining conversation history and providing real-time monitoring.

## Features

- **Multi-modal Communication**
  - Text-based chat with Claude AI
  - Voice message support (Speech-to-Text)
  - AI responses in both text and voice formats (Text-to-Speech)
  - Multi-language support based on user's Telegram language settings

- **Intelligent Conversation**
  - Powered by Claude 3 Opus for natural language understanding
  - Maintains conversation history for context
  - Configurable personality and behavior
  - Auto-deletion of old conversations after 30 days

- **Production-Ready Infrastructure**
  - Docker containerization
  - MongoDB for persistent storage
  - Automatic container health checks
  - Configurable through environment variables

- **Comprehensive Monitoring**
  - Sentry integration for error tracking and performance monitoring
  - Prometheus metrics collection
  - Grafana dashboards for real-time monitoring
  - Key metrics tracking:
    - Message processing rates
    - API response times
    - Error rates
    - Voice processing performance

## Prerequisites

- Docker and Docker Compose
- FFmpeg (for audio processing)
- Telegram Bot Token
- Anthropic API Key
- Sentry DSN (optional but recommended)

## Quick Start

1. Clone the repository:
```bash
git clone https://github.com/yourusername/telegram-anthropic-voice.git
cd telegram-anthropic-voice
```

2. Create a `.env` file with your configuration:
```env
TELEGRAM_TOKEN=your_telegram_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
BOT_NAME=Sophie
BOT_PERSONALITY="You are Sophie, a friendly and helpful assistant."
MONGO_USER=root
MONGO_PASSWORD=your_secure_password
SENTRY_DSN=your_sentry_dsn
GRAFANA_PASSWORD=your_grafana_password
```

3. Start the services:
```bash
docker-compose up -d
```

4. Access the monitoring interfaces:
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

## Architecture

The bot is built with several key components:

- **Bot Core**: Handles Telegram interactions and message routing
- **AudioProcessor**: Manages voice message conversion (STT/TTS)
- **ChatHistory**: Handles conversation persistence in MongoDB
- **Monitoring Stack**: Sentry, Prometheus, and Grafana integration

## Configuration Options

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| BOT_NAME | Name of the bot | Sophie |
| BOT_PERSONALITY | System prompt for Claude | "You are Sophie..." |
| MESSAGE_HISTORY_LIMIT | Number of messages to keep for context | 10 |
| SPEECH_SPEED | Voice response speed multiplier | 1.3 |

## Monitoring & Metrics

The bot exposes the following metrics:

- `messages_processed_total`: Counter of processed messages by type
- `claude_request_duration_seconds`: Histogram of Claude API response times
- `claude_errors_total`: Counter of Claude API errors
- `voice_processing_errors_total`: Counter of voice processing errors

## Development

### Requirements

```txt
python-telegram-bot
anthropic
pydub
transformers
pymongo
gtts
sentry-sdk[pymongo]
prometheus_client
python-dotenv
```

### Local Development

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create `.env` file with required environment variables

3. Run the bot:
```bash
python bot.py
```

## Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

MIT License - see LICENSE file for details

## Support

For issues and feature requests, please create an issue in the GitHub repository.
