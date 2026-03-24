"""Video Distiller Router Prompt

This prompt instructs the Lead Agent to automatically delegate video link
processing to the video-distiller sub-agent.
"""

VIDEO_DISTILLER_ROUTING_PROMPT = """
## Video Link Detection & Routing

When you detect a video URL in the user's message, automatically delegate to the video-distiller sub-agent.

### Detection Patterns

Recognize these video platform URLs:
- Bilibili: `bilibili.com/video/`, `b23.tv/*`
- YouTube: `youtube.com/watch`, `youtu.be/*`

### Routing Rule

If the user's message contains a video URL:
1. **Do NOT** try to fetch the URL yourself with web_fetch
2. **Do NOT** try to summarize based on page content alone
3. **DO** delegate to the video-distiller sub-agent with the task:
   "Extract and structure knowledge from this video: {url}"

### Why

The video-distiller sub-agent has:
- Access to the distill_video MCP tool
- yt-dlp and ffmpeg for audio extraction
- Whisper for ASR transcription
- Structured output formatting

Attempting to process video links without these tools will result in incomplete or inaccurate summaries.

### Exception

Only handle video links manually if:
- The video-distiller sub-agent is unavailable
- The user explicitly asks you NOT to use the video tool
- The video is behind authentication you cannot bypass
"""
