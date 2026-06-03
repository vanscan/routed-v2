# Supabase OAuth Configuration Guide

## 📋 Overview
This guide helps you set up Google, GitHub, and Apple OAuth providers in your Supabase project.

---

## 🔐 1. Google OAuth Setup

### Step 1: Google Cloud Console
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Navigate to **APIs & Services** → **Credentials**

### Step 2: Create OAuth Client
1. Click **+ CREATE CREDENTIALS** → **OAuth client ID**
2. Application type: **Web application**
3. Name: `PathPilot Supabase Auth`

### Step 3: Configure Redirect URIs
Add these **Authorized redirect URIs**:
```
https://dxntrjgyxmchhgocpoim.supabase.co/auth/v1/callback
```

For local development, also add:
```
http://localhost:3000/auth/callback
```

### Step 4: Get Credentials
Copy your:
- **Client ID**: `xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com`
- **Client Secret**: `GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxxx`

### Step 5: Configure in Supabase
1. Go to your [Supabase Dashboard](https://supabase.com/dashboard)
2. Select your project
3. Navigate to **Authentication** → **Providers**
4. Find **Google** and click to expand
5. Toggle **Enable Sign in with Google** ON
6. Paste your Client ID and Client Secret
7. Click **Save**

---

## 🐙 2. GitHub OAuth Setup

### Step 1: GitHub Developer Settings
1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click **OAuth Apps** → **New OAuth App**

### Step 2: Register Application
Fill in:
- **Application name**: `PathPilot`
- **Homepage URL**: `https://your-app-domain.com` (or localhost for dev)
- **Authorization callback URL**: 
  ```
  https://dxntrjgyxmchhgocpoim.supabase.co/auth/v1/callback
  ```

### Step 3: Get Credentials
After creating, you'll see:
- **Client ID**: Copy this
- **Client Secret**: Click **Generate a new client secret** and copy

### Step 4: Configure in Supabase
1. Go to Supabase Dashboard → **Authentication** → **Providers**
2. Find **GitHub** and expand
3. Toggle **Enable Sign in with GitHub** ON
4. Paste Client ID and Client Secret
5. Click **Save**

---

## 🍎 3. Apple OAuth Setup

### Prerequisites
- Apple Developer Account ($99/year)
- App registered in Apple Developer Portal

### Step 1: Apple Developer Portal
1. Go to [Apple Developer Portal](https://developer.apple.com/)
2. Navigate to **Certificates, Identifiers & Profiles**

### Step 2: Create App ID
1. Go to **Identifiers** → Click **+**
2. Select **App IDs** → Continue
3. Select **App** → Continue
4. Fill in:
   - Description: `PathPilot`
   - Bundle ID: `com.yourcompany.pathpilot` (Explicit)
5. Scroll down, check **Sign in with Apple**
6. Click **Continue** → **Register**

### Step 3: Create Service ID
1. Go to **Identifiers** → Click **+**
2. Select **Services IDs** → Continue
3. Fill in:
   - Description: `PathPilot Web Auth`
   - Identifier: `com.yourcompany.pathpilot.web`
4. Click **Continue** → **Register**
5. Click on your new Service ID
6. Check **Sign in with Apple** → Click **Configure**
7. Set:
   - Primary App ID: Select your App ID
   - Domains: `dxntrjgyxmchhgocpoim.supabase.co`
   - Return URLs: `https://dxntrjgyxmchhgocpoim.supabase.co/auth/v1/callback`
8. Click **Save** → **Continue** → **Save**

### Step 4: Create Key
1. Go to **Keys** → Click **+**
2. Key Name: `PathPilot Sign in with Apple`
3. Check **Sign in with Apple** → Click **Configure**
4. Select your Primary App ID
5. Click **Save** → **Continue** → **Register**
6. **Download the key file** (`.p8`) - you can only download once!
7. Note the **Key ID**

### Step 5: Get Your Team ID
Your Team ID is in the top-right of the Apple Developer portal, or go to **Membership** to find it.

### Step 6: Configure in Supabase
1. Go to Supabase Dashboard → **Authentication** → **Providers**
2. Find **Apple** and expand
3. Toggle **Enable Sign in with Apple** ON
4. Fill in:
   - **Service ID**: `com.yourcompany.pathpilot.web`
   - **Team ID**: Your Apple Team ID
   - **Key ID**: From Step 4
   - **Private Key**: Paste contents of your `.p8` file
5. Click **Save**

---

## 🔧 4. Update Frontend Redirect URLs

Once OAuth is configured, update your app's redirect handling. The current implementation in `SupabaseContext.tsx` already handles redirects:

```typescript
// In signInWithOAuth
const { error } = await supabaseClient.auth.signInWithOAuth({
  provider,
  options: {
    redirectTo: Platform.OS === 'web' 
      ? window.location.origin  // Web: redirects back to your app
      : undefined,              // Native: uses deep linking
  },
});
```

### For Native Apps (iOS/Android)
Add URL scheme to `app.json`:

```json
{
  "expo": {
    "scheme": "pathpilot",
    "ios": {
      "bundleIdentifier": "com.yourcompany.pathpilot"
    },
    "android": {
      "package": "com.yourcompany.pathpilot"
    }
  }
}
```

Then configure deep link handling in Supabase Dashboard:
- **Site URL**: `https://your-production-domain.com`
- **Redirect URLs**: Add `pathpilot://` for native apps

---

## 📱 5. Testing OAuth

### Test on Web
1. Run your app: `npx expo start --web`
2. Click "Continue with Google" (or other provider)
3. Complete OAuth flow
4. Verify redirect back to your app

### Test on Mobile (Expo Go)
1. Scan QR code with Expo Go
2. Test OAuth flow
3. Note: Some OAuth providers may require a standalone build

### Verify in Supabase
1. Go to Supabase Dashboard → **Authentication** → **Users**
2. You should see new users created via OAuth

---

## 🔒 6. Security Checklist

- [ ] Redirect URLs match exactly (no trailing slashes)
- [ ] Client secrets are never exposed in frontend code
- [ ] PKCE flow is enabled (default in Supabase)
- [ ] Production URLs are configured before going live
- [ ] Test all OAuth flows before launch

---

## 🆘 Troubleshooting

### "redirect_uri_mismatch" Error
- Ensure the redirect URI in provider matches exactly what Supabase expects
- Check for trailing slashes
- Verify you're using the correct Supabase project URL

### "invalid_client" Error
- Double-check Client ID and Secret
- Ensure provider is enabled in Supabase
- Regenerate credentials if needed

### OAuth Works on Web but Not Mobile
- Configure URL schemes in `app.json`
- Test with a development build (not Expo Go for some providers)
- Check deep link configuration

---

## 📞 Support

- [Supabase Auth Documentation](https://supabase.com/docs/guides/auth)
- [Google OAuth Setup](https://supabase.com/docs/guides/auth/social-login/auth-google)
- [GitHub OAuth Setup](https://supabase.com/docs/guides/auth/social-login/auth-github)
- [Apple OAuth Setup](https://supabase.com/docs/guides/auth/social-login/auth-apple)
