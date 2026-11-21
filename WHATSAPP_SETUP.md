# Local WhatsApp test

1. Create a Meta developer app with WhatsApp. In API Setup, add and verify your personal number as a test recipient.
2. Add these values to `backend/.env`: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` (a random secret you choose), and `WHATSAPP_TEST_RECIPIENT` (country code plus digits, without `+`). Optional: `WHATSAPP_API_VERSION=v24.0`.
3. Restart the backend. Keep the dashboard local. Expose only GET and POST `/api/whatsapp/webhook` through a public HTTPS tunnel or reverse proxy; other routes contain visit data and are not authenticated.
4. In Meta's WhatsApp webhook configuration, set the public URL ending in `/api/whatsapp/webhook`, enter the same verify token, and subscribe to `messages` events for the WhatsApp Business Account.
5. Send a message from your verified personal phone to Meta's test number. This opens the 24-hour window for interactive replies. If needed, send Meta's sample template from API Setup first and reply to it.
6. In SwarVani, save a demo visit containing a follow-up, open Follow-ups, and click Send test follow-up. The destination is the server-configured test phone, not the beneficiary. No clinical details are sent.
7. Tap Confirm, Reschedule, or Request ASHA call on your phone. The board polls for updates every five seconds.

An accepted send is not a delivery confirmation. If Meta rejects a request, check the token expiry, verified recipient, phone number ID, and reply window. Do not retry blindly after a timeout.

This is a local test flow, not a production reminder service. Production needs authentication, recipient mapping and consent, approved templates for reminders outside the reply window, and delivery tracking. No credentials means no real message can be sent.
