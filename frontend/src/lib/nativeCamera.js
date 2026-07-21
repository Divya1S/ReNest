/**
 * Thin wrapper around @capacitor/camera with an HTML <input> fallback for web.
 * Returns a File object regardless of platform so callers need no branching.
 */

function isNative() {
  try {
    return (
      typeof window !== "undefined" &&
      window.Capacitor?.isNativePlatform?.() === true
    );
  } catch {
    return false;
  }
}

/**
 * Open the camera or photo library.
 * @param {"camera"|"photos"} source
 * @returns {Promise<File>}
 */
export async function pickImage(source = "camera") {
  if (isNative()) {
    const { Camera, CameraSource, CameraResultType } = await import(
      "@capacitor/camera"
    );
    const sourceMap = {
      camera: CameraSource.Camera,
      photos: CameraSource.Photos,
    };
    const photo = await Camera.getPhoto({
      quality: 85,
      allowEditing: false,
      resultType: CameraResultType.Blob,
      source: sourceMap[source] ?? CameraSource.Prompt,
    });
    return new File([photo.blob], `photo_${Date.now()}.jpg`, {
      type: photo.format === "png" ? "image/png" : "image/jpeg",
    });
  }

  // Web fallback — programmatically click a hidden file input
  return new Promise((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    if (source === "camera") input.capture = "environment";
    input.onchange = () => {
      const file = input.files?.[0];
      if (file) resolve(file);
      else reject(new Error("No file selected"));
    };
    input.oncancel = () => reject(new Error("Cancelled"));
    input.click();
  });
}

/**
 * Open photo library for multi-select (web only — native shows one at a time).
 * @returns {Promise<File[]>}
 */
export async function pickMultipleImages() {
  if (isNative()) {
    // Capacitor camera doesn't support bulk select; call pickImage sequentially
    // until the user cancels. For now, just pick one.
    const file = await pickImage("photos");
    return [file];
  }

  return new Promise((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = true;
    input.onchange = () => {
      const files = Array.from(input.files ?? []);
      if (files.length) resolve(files);
      else reject(new Error("No files selected"));
    };
    input.oncancel = () => reject(new Error("Cancelled"));
    input.click();
  });
}
