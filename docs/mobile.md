# ReNest Mobile (Capacitor)

ReNest uses [Capacitor](https://capacitorjs.com/) to wrap the existing Vite/React web app in a native iOS and Android shell. There is **no separate mobile codebase** — the same `src/` tree runs on web, iOS, and Android.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Node | 20+ |
| Xcode | 16+ (macOS only, for iOS) |
| Android Studio | Ladybug+ (for Android) |
| CocoaPods | 1.15+ (`sudo gem install cocoapods`) |

---

## First-time setup

```bash
# 1. Install JS dependencies (includes @capacitor/* packages)
cd frontend
npm install

# 2. Build the web app
npm run build

# 3. Add native platforms (only needed once per machine)
npx cap add ios
npx cap add android

# 4. Sync web build + plugins into native projects
npm run cap:sync
```

---

## Daily development

```bash
# After any frontend code change:
npm run build && npm run cap:sync

# Open Xcode (iOS simulator or device)
npm run cap:open:ios

# Open Android Studio (emulator or device)
npm run cap:open:android
```

For rapid iteration on the web layer, use `npm run dev` — the browser DevTools experience is faster than recompiling native. Use the native shell only to test camera, push notifications, or offline behaviour.

---

## Native camera

`src/lib/nativeCamera.js` exports `pickImage(source)` and `pickMultipleImages()`. They call `@capacitor/camera` when running inside the native shell and fall back to a programmatic `<input type="file">` on web. No component needs an `if (native)` branch.

```js
import { pickImage } from "../lib/nativeCamera";

const file = await pickImage("camera");   // native camera sheet on iOS/Android
const file = await pickImage("photos");   // photo library picker
```

---

## Offline scan queue

`src/lib/scanQueue.js` buffers `POST /api/scan-sessions` and item mutations when `navigator.onLine` is false. Items are stored in `localStorage` (web) or `@capacitor/preferences` (native). The queue replays automatically on the browser `online` event, wired up in `App.jsx`.

```js
import { ScanQueue } from "../lib/scanQueue";

// Enqueue a failed mutation manually
await ScanQueue.enqueue({ method: "POST", url: "/scan-sessions", body: data });

// Check pending count for UI badge
const n = await ScanQueue.size();
```

---

## Native push notifications (APNs / FCM)

After requesting permission via `@capacitor/push-notifications`, register the device token with the backend:

```js
import { PushNotifications } from "@capacitor/push-notifications";

await PushNotifications.requestPermissions();
await PushNotifications.register();

PushNotifications.addListener("registration", ({ value: token }) => {
  apiFetch("/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      platform: Capacitor.getPlatform() === "ios" ? "apns" : "fcm",
      token,
    }),
  });
});
```

The backend `PushSubscription` model stores the token with `platform=apns|fcm`. Delivery routing through APNs/FCM is handled server-side in the push task.

---

## App Store / Play Store submission checklist

- [ ] App icons: 1024×1024 PNG at `ios/App/App/Assets.xcassets/AppIcon.appiconset/`
- [ ] Splash screen: configured via `@capacitor/splash-screen` in `capacitor.config.json`
- [ ] `NSCameraUsageDescription` in `ios/App/App/Info.plist`
- [ ] `NSPhotoLibraryUsageDescription` in `ios/App/App/Info.plist`
- [ ] Privacy Policy URL in App Store Connect
- [ ] Age rating: 4+
- [ ] TestFlight internal testing track created before external review

---

## Troubleshooting

**`cap sync` fails with pod install error**
```bash
cd ios/App && pod install --repo-update
```

**White screen on device after build**
Check that `webDir` in `capacitor.config.json` matches `outDir` in `vite.config.js` (both should be `dist`).

**Camera permission denied on iOS simulator**
The simulator does not support the camera hardware. Use a physical device or test the web fallback in a browser.
