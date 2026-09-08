import { ArrowRight, ImagePlus, Images, Sparkles, TrendingUp, X } from "lucide-react";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import DraftStatusNotice from "../components/DraftStatusNotice";
import PageSection from "../components/PageSection";
import SkeletonLoader from "../components/SkeletonLoader";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { useAuth } from "../context/AuthContext";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePersistentDraftState } from "../hooks/usePersistentDraftState";
import { useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { apiFetch, toLocalDateTimeInput } from "../lib/api";
import type { Listing, ListingGalleryImage, PricingHint } from "../types";

const initialForm = {
  title: "",
  description: "",
  category: "storage",
  condition: "good",
  price_type: "free",
  price_amount: "",
  estimated_retail_value: "",
  pickup_zone: "",
  building: "",
  available_until: "",
  image: null as File | null,
};

function normalizeFormForDirtyCheck(form: typeof initialForm) {
  return {
    ...form,
    image: Boolean(form.image),
  };
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

function computeCompleteness(form: typeof initialForm, hasImage: boolean) {
  let score = 0;
  if ((form.title ?? "").trim().length >= 3) score += 20;
  if ((form.description ?? "").trim().length >= 40) score += 20;
  if (hasImage) score += 20;
  if (parseFloat(form.estimated_retail_value) > 0) score += 20;
  if ((form.pickup_zone ?? "").trim().length >= 10) score += 20;
  return score;
}

export default function ListingFormPage() {
  const { listingId } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(listingId);
  const { user } = useAuth();
  usePageTitle(editing ? "Edit Listing" : "Create Listing");
   
  const createDraft = usePersistentDraftState({
    key: "listing-create",
    initialValue: initialForm,
    enabled: !editing,
    serialize: (value: typeof initialForm) => ({
      ...value,
      image: null,
    }),
  } as any);
  const [editingForm, setEditingForm] = useState(initialForm);
  const [loadedEditingForm, setLoadedEditingForm] = useState(initialForm);
  const [previewUrl, setPreviewUrl] = useState("");
  const [loading, setLoading] = useState(editing);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [generatingDesc, setGeneratingDesc] = useState(false);
  const [pricingHint, setPricingHint] = useState<PricingHint | null>(null);
  const [extraFiles, setExtraFiles] = useState<File[]>([]);
  const [galleryImages, setGalleryImages] = useState<ListingGalleryImage[]>([]);
  const MAX_EXTRA_PHOTOS = 4;
  const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
  const form = editing ? editingForm : createDraft.state;
  const setForm = editing ? setEditingForm : createDraft.setState;

  // Auto-populate available_until from campus move-out date when creating a new listing
  useEffect(() => {
    if (editing || form.available_until || !user?.campus?.move_out_end) return;
    const moveOutDate = new Date(user.campus.move_out_end + "T23:59");
    if (moveOutDate > new Date()) {
      setForm((c) => ({ ...c, available_until: toLocalDateTimeInput(moveOutDate.toISOString()) }));
    }
  }, [editing, user?.campus?.move_out_end]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!editing) {
      return undefined;
    }

    let active = true;
    apiFetch<Listing>(`/listings/${listingId}`)
      .then((listing) => {
        if (active) {
          const nextForm = {
            title: listing.title,
            description: listing.description,
            category: listing.category,
            condition: listing.condition,
            price_type: listing.price_type,
            price_amount: listing.price_type === "low_cost" ? String(listing.price_amount ?? "") : "",
            estimated_retail_value: String(listing.estimated_retail_value ?? ""),
            pickup_zone: listing.pickup_zone,
            building: listing.building || "",
            available_until: toLocalDateTimeInput(listing.available_until ?? ""),
            image: null as File | null,
          };
          setEditingForm(nextForm);
          setLoadedEditingForm(nextForm);
          setPreviewUrl(listing.image_url || "");
          setGalleryImages(listing.gallery ?? []);
        }
      })
      .catch((requestError) => {
        if (active) {
          setError((requestError as Error).message);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [editing, listingId]);

  const localPreview = useMemo(() => {
    if (!form.image) {
      return "";
    }
    return URL.createObjectURL(form.image);
  }, [form.image]);

  const extraPreviews = useMemo(
    () => extraFiles.map((file) => URL.createObjectURL(file)),
    [extraFiles],
  );

  useEffect(() => {
    return () => {
      extraPreviews.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [extraPreviews]);

  useEffect(() => {
    return () => {
      if (localPreview) {
        URL.revokeObjectURL(localPreview);
      }
    };
  }, [localPreview]);

  const debouncedCategory = useDebounce(form.category, 400);
  const debouncedCondition = useDebounce(form.condition, 400);

  useEffect(() => {
    if (!debouncedCategory || !debouncedCondition) return;
    let active = true;
    apiFetch<PricingHint>(`/listings/pricing-hint?category=${debouncedCategory}&condition=${debouncedCondition}`)
      .then((data) => { if (active && data.sample_size > 0) setPricingHint(data); })
      .catch(() => {});
    return () => { active = false; };
  }, [debouncedCategory, debouncedCondition]);

  const handleGenerateDescription = useCallback(async () => {
    if (!form.title.trim()) {
      toast.error("Add a title first so AI knows what to describe.");
      return;
    }
    setGeneratingDesc(true);
    try {
      const data = await apiFetch<{ description: string }>("/listings/generate-description", {
        method: "POST",
        body: {
          title: form.title,
          category: form.category,
          condition: form.condition,
          price_type: form.price_type,
          estimated_retail_value: form.estimated_retail_value || undefined,
        },
      });
      setForm((c) => ({ ...c, description: data.description }));
      clearFieldError("description");
      toast.success("Description written — edit it to make it yours.");
    } catch (err) {
      toast.error((err as Error).message || "AI unavailable — please write manually.");
    } finally {
      setGeneratingDesc(false);
    }
  }, [form.title, form.category, form.condition, form.price_type, form.estimated_retail_value, setForm]); // eslint-disable-line react-hooks/exhaustive-deps

  const completeness = computeCompleteness(form, Boolean(localPreview || previewUrl));

  const hasUnsavedChanges = editing
    ? JSON.stringify(normalizeFormForDirtyCheck(editingForm)) !==
        JSON.stringify(normalizeFormForDirtyCheck(loadedEditingForm)) || extraFiles.length > 0
    : createDraft.isDirty || Boolean(form.image) || extraFiles.length > 0;

  const unsavedGuard = useUnsavedChangesGuard({
    when: hasUnsavedChanges && !submitting,
    title: editing ? "Leave without saving this listing?" : "Leave before posting this listing?",
    message: editing
      ? "Your listing edits have not been saved yet. Leaving now will discard the in-progress changes on this page."
      : "Your listing draft is still in progress. Leaving now may interrupt posting, even though the text fields are autosaved locally.",
  });

  // Mirrors the server's limits (listings/image_utils.py) so the common
  // rejections are caught before a slow upload, not after it.
  function rejectUnusableImage(file: File): boolean {
    if (!file.type.startsWith("image/")) {
      toast.error(`${file.name} is not an image.`);
      return true;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      toast.error(`${file.name} is larger than 10 MB. Try a smaller photo.`);
      return true;
    }
    return false;
  }

  function handleAddExtraPhotos(files: File[]) {
    const room = MAX_EXTRA_PHOTOS - galleryImages.length - extraFiles.length;
    if (room <= 0) {
      toast.error(`You can attach up to ${MAX_EXTRA_PHOTOS} extra photos.`);
      return;
    }
    const usable = files.filter((file) => !rejectUnusableImage(file));
    if (!usable.length) return;
    if (usable.length > room) {
      toast.info(`Only ${room} more photo${room !== 1 ? "s" : ""} can be added.`);
    }
    setExtraFiles((current) => [...current, ...usable.slice(0, room)]);
  }

  async function handleRemoveGalleryImage(imageId: number) {
    try {
      const data = await apiFetch<{ listing: Listing }>(
        `/listings/${listingId}/images/${imageId}`,
        { method: "DELETE" },
      );
      setGalleryImages(data.listing.gallery ?? []);
      toast.success("Photo removed.");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  function validateFields(fields: typeof initialForm): Record<string, string> {
    const errs: Record<string, string> = {};
    const title = (fields.title ?? "").trim();
    if (!title) errs.title = "Title is required.";
    else if (title.length < 3) errs.title = "Title must be at least 3 characters.";
    else if (title.length > 120) errs.title = "Title must be 120 characters or fewer.";

    const description = (fields.description ?? "").trim();
    if (!description) errs.description = "Description is required.";
    else if (description.length < 10) errs.description = "Description must be at least 10 characters.";
    else if (description.length > 2000) errs.description = "Description must be 2000 characters or fewer.";

    const pickupZone = (fields.pickup_zone ?? "").trim();
    if (!pickupZone) errs.pickup_zone = "Pickup zone is required.";
    else if (pickupZone.length < 3) errs.pickup_zone = "Pickup zone must be at least 3 characters.";

    if (!fields.available_until) {
      errs.available_until = "Move-out deadline is required.";
    } else if (new Date(fields.available_until) <= new Date()) {
      errs.available_until = "Deadline must be in the future.";
    }

    if (fields.price_type === "low_cost") {
      const amount = parseFloat(fields.price_amount);
      if (!fields.price_amount || isNaN(amount) || amount <= 0) {
        errs.price_amount = "Enter a price greater than $0 for low-cost items.";
      }
    }

    return errs;
  }

  function handleBlur(field: string) {
    const errs = validateFields(form);
    if (errs[field]) {
      setFieldErrors((current) => ({ ...current, [field]: errs[field] }));
    } else {
      setFieldErrors((current) => {
        const next = { ...current };
        delete next[field];
        return next;
      });
    }
  }

  function clearFieldError(field: string) {
    if (fieldErrors[field]) {
      setFieldErrors((current) => {
        const next = { ...current };
        delete next[field];
        return next;
      });
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const errs = validateFields(form);
    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs);
      return;
    }
    setSubmitting(true);
    setError("");

    try {
      // Step 1: create/update listing metadata (no image body yet for new listings)
      const metaPayload = {
        title: form.title,
        description: form.description,
        category: form.category,
        condition: form.condition,
        price_type: form.price_type,
        price_amount: form.price_type === "low_cost" ? form.price_amount || "0" : "0",
        estimated_retail_value: form.estimated_retail_value || "0",
        pickup_zone: form.pickup_zone,
        available_until: new Date(form.available_until).toISOString(),
        ...(form.building ? { building: form.building } : {}),
      };

      let listing = await apiFetch<Listing>(editing ? `/listings/${listingId}` : "/listings", {
        method: editing ? "PATCH" : "POST",
        body: metaPayload,
      });

      // Step 2: upload image via presigned S3 URL, or fall back to multipart PATCH.
      // The listing already exists at this point, so a rejected photo must not
      // send the user back to an empty create form, because resubmitting there would
      // create a second listing.
      if (form.image) {
        try {
          const uploaded = await _uploadImage(form.image, listing.id);
          if (!uploaded) {
            // Fallback: send as multipart
            const fd = new FormData();
            fd.append("image", form.image as File);
            listing = await apiFetch<Listing>(`/listings/${listing.id}`, { method: "PATCH", body: fd });
          }
        } catch (imageError) {
          toast.error(
            `Listing saved, but the cover photo was rejected: ${(imageError as Error).message}`,
          );
          if (!editing) createDraft.clearDraft({ reset: true });
          navigate(`/listings/${listing.id}/edit`, { replace: true });
          return;
        }
      }

      // Step 3: upload any extra gallery photos
      if (extraFiles.length) {
        try {
          const galleryPayload = new FormData();
          extraFiles.forEach((file) => galleryPayload.append("images", file));
          await apiFetch(`/listings/${listing.id}/images`, { method: "POST", body: galleryPayload });
        } catch (galleryError) {
          toast.error(
            `Listing saved, but some extra photos failed to upload: ${(galleryError as Error).message}`,
          );
        }
      }

      toast.success(editing ? "Listing updated." : "Listing created.");
      if (!editing) createDraft.clearDraft({ reset: true });
      navigate(`/listings/${listing.id}`, { replace: true });
    } catch (requestError) {
      setError((requestError as Error).message);
      toast.error((requestError as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  async function _uploadImage(file: File, listingId: number) {
    type UploadUrlResponse = { upload_url?: string; cdn_url?: string; status?: number };
    try {
      const { upload_url, cdn_url, status } = await apiFetch<UploadUrlResponse>(`/listings/${listingId}/upload-url`, {
        method: "POST",
        body: { content_type: file.type, content_length: file.size },
      }).catch(() => ({} as UploadUrlResponse));

      if (!upload_url || status === 501) return false;

      const s3Res = await fetch(upload_url, {
        method: "PUT",
        body: file,
        headers: { "Content-Type": file.type },
      });
      if (!s3Res.ok) return false;

      await apiFetch(`/listings/${listingId}`, {
        method: "PATCH",
        body: { image_cdn_url: cdn_url },
      });
      return true;
    } catch {
      return false;
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl space-y-6">
        <SkeletonLoader className="h-24 !rounded-[2rem]" />
        <div className="grid gap-4 sm:grid-cols-2">
          <SkeletonLoader className="h-14 sm:col-span-2 !rounded-2xl" />
          <SkeletonLoader className="h-32 sm:col-span-2 !rounded-2xl" />
          <SkeletonLoader className="h-14 !rounded-2xl" />
          <SkeletonLoader className="h-14 !rounded-2xl" />
          <SkeletonLoader className="h-14 !rounded-2xl" />
          <SkeletonLoader className="h-14 !rounded-2xl" />
          <SkeletonLoader className="h-14 !rounded-2xl" />
          <SkeletonLoader className="h-14 !rounded-2xl" />
        </div>
      </div>
    );
  }

  return (
    <>
      <UnsavedChangesDialog {...unsavedGuard.dialogProps} />
      <PageSection className="mx-auto max-w-5xl paper-panel p-8 sm:p-12">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="label-title">{editing ? "Edit listing" : "Create listing"}</p>
            <h1 className="mt-3 text-[28px] font-bold tracking-[-0.02em]">
              {editing ? "Update your pickup details before move-out." : "Post a dorm essential before it gets thrown away."}
            </h1>
          </div>
          {!editing && (
            <div className="mt-4 sm:mt-0 sm:w-40 shrink-0">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Completeness</span>
                <span className={`text-[11px] font-bold ${completeness === 100 ? "text-emerald-500" : "text-[color:var(--text-muted)]"}`}>{completeness}%</span>
              </div>
              <div className="h-2 w-full rounded-full bg-[color:var(--color-surface-2)] overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${completeness === 100 ? "bg-emerald-500" : completeness >= 60 ? "bg-[color:var(--color-accent)]" : "bg-amber-400"}`}
                  style={{ width: `${completeness}%` }}
                />
              </div>
              {completeness < 100 && (
                <p className="mt-1 text-[10px] text-[color:var(--text-muted)]">Higher scores rank better</p>
              )}
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} className="mt-8 grid gap-4 sm:grid-cols-2">
          {!editing ? (
            <div className="sm:col-span-2">
              <DraftStatusNotice
                lastSavedAt={createDraft.lastSavedAt}
                hasRestoredDraft={createDraft.hasRestoredDraft}
                detail="Text fields are autosaved locally. Image uploads still need to be reattached."
                onDiscard={() => createDraft.clearDraft({ reset: true })}
              />
            </div>
          ) : null}
          {editing && hasUnsavedChanges ? (
            <div className="sm:col-span-2">
              <DraftStatusNotice
                lastSavedAt={null}
                hasRestoredDraft={true}
                onDiscard={undefined}
                message="Unsaved listing edits in progress"
                detail="Save your changes before leaving this page."
              />
            </div>
          ) : null}
        <label className="sm:col-span-2 flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Item Title</span>
          <input
            className={`field ${fieldErrors.title ? "border-red-400 focus:border-red-400 focus:ring-red-200" : ""}`}
            placeholder="e.g. Dorm fan, Storage bin, Desk lamp"
            value={form.title}
            onChange={(event) => { setForm((c) => ({ ...c, title: event.target.value })); clearFieldError("title"); }}
            onBlur={() => handleBlur("title")}
          />
          {fieldErrors.title && <p className="text-xs font-semibold text-red-500">{fieldErrors.title}</p>}
        </label>

        <div className="sm:col-span-2 flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <label htmlFor="listing-description" className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Description</label>
            <button
              type="button"
              onClick={handleGenerateDescription}
              disabled={generatingDesc}
              className="flex items-center gap-1.5 rounded-full bg-[color:var(--color-tag-soft)] px-3 py-1 text-[11px] font-semibold text-[color:var(--color-tag)] transition hover:bg-[color:var(--color-tag)] hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Sparkles size={12} />
              {generatingDesc ? "Writing…" : "Write with AI"}
            </button>
          </div>
          <textarea
            id="listing-description"
            className={`field min-h-36 ${fieldErrors.description ? "border-red-400 focus:border-red-400 focus:ring-red-200" : ""}`}
            placeholder="Describe condition, what is included, and anything the next student should know."
            value={form.description}
            onChange={(event) => { setForm((c) => ({ ...c, description: event.target.value })); clearFieldError("description"); }}
            onBlur={() => handleBlur("description")}
          />
          {fieldErrors.description && <p className="text-xs font-semibold text-red-500">{fieldErrors.description}</p>}
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Category</span>
          <select
            className="field"
            value={form.category}
            onChange={(event) => setForm((current) => ({ ...current, category: event.target.value }))}
          >
            <option value="storage">Storage</option>
            <option value="lighting">Lighting</option>
            <option value="supplies">School supplies</option>
            <option value="comfort">Comfort</option>
            <option value="toiletries">Toiletries</option>
            <option value="decor">Decor</option>
            <option value="other">Other</option>
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Condition</span>
          <select
            className="field"
            value={form.condition}
            onChange={(event) => setForm((current) => ({ ...current, condition: event.target.value }))}
          >
            <option value="new">Like new</option>
            <option value="good">Good</option>
            <option value="fair">Fair</option>
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Pricing</span>
          <select
            className="field"
            value={form.price_type}
            onChange={(event) => setForm((current) => ({ ...current, price_type: event.target.value }))}
          >
            <option value="free">Free</option>
            <option value="low_cost">Low cost</option>
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
            Price
            {form.price_type !== "low_cost" && (
              <span className="ml-2 font-medium normal-case tracking-normal text-[color:var(--text-muted)]">— set pricing to &quot;Low cost&quot; to enable</span>
            )}
          </span>
          <input
            className={`field disabled:cursor-not-allowed disabled:opacity-50 ${fieldErrors.price_amount ? "border-red-400 focus:border-red-400 focus:ring-red-200" : ""}`}
            type="number"
            min="0.01"
            step="0.01"
            placeholder="0.00"
            value={form.price_amount}
            onChange={(event) => { setForm((c) => ({ ...c, price_amount: event.target.value })); clearFieldError("price_amount"); }}
            onBlur={() => handleBlur("price_amount")}
            disabled={form.price_type !== "low_cost"}
          />
          {fieldErrors.price_amount && <p className="text-xs font-semibold text-red-500">{fieldErrors.price_amount}</p>}
        </label>

        {pricingHint && (
          <div className="sm:col-span-2 flex items-start gap-2.5 rounded-2xl bg-[color:var(--color-tag-soft)] px-4 py-3 text-sm text-[color:var(--color-tag)]">
            <TrendingUp size={15} className="mt-0.5 shrink-0" />
            <span className="font-medium">{pricingHint.suggestion}</span>
          </div>
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Estimated Retail Value</span>
          <input
            className="field"
            type="number"
            min="0"
            step="0.01"
            placeholder="e.g. 24.00"
            value={form.estimated_retail_value}
            onChange={(event) => setForm((current) => ({ ...current, estimated_retail_value: event.target.value }))}
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Pickup Zone</span>
          <input
            className={`field ${fieldErrors.pickup_zone ? "border-red-400 focus:border-red-400 focus:ring-red-200" : ""}`}
            placeholder="e.g. Dorm A lobby, Building 3 entry"
            value={form.pickup_zone}
            onChange={(event) => { setForm((c) => ({ ...c, pickup_zone: event.target.value })); clearFieldError("pickup_zone"); }}
            onBlur={() => handleBlur("pickup_zone")}
          />
          {fieldErrors.pickup_zone && <p className="text-xs font-semibold text-red-500">{fieldErrors.pickup_zone}</p>}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Building / Dorm <span className="normal-case font-normal opacity-60">(optional)</span></span>
          <input
            className="field"
            placeholder="e.g. Marks Hall, North Tower, Building 7"
            value={form.building}
            onChange={(event) => setForm((c) => ({ ...c, building: event.target.value }))}
          />
          <p className="text-[11px] text-[color:var(--text-muted)]">Helps nearby students find your item faster.</p>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Available Until</span>
          <input
            className={`field ${fieldErrors.available_until ? "border-red-400 focus:border-red-400 focus:ring-red-200" : ""}`}
            type="datetime-local"
            value={form.available_until}
            onChange={(event) => { setForm((c) => ({ ...c, available_until: event.target.value })); clearFieldError("available_until"); }}
            onBlur={() => handleBlur("available_until")}
          />
          {!editing && user?.campus?.move_out_end && (
            <p className="text-[11px] text-[color:var(--text-muted)]">
              Pre-filled with {user.campus.name}&apos;s move-out date. You can adjust it.
            </p>
          )}
          {fieldErrors.available_until && <p className="text-xs font-semibold text-red-500">{fieldErrors.available_until}</p>}
        </label>
        <div className="sm:col-span-2 space-y-3">
          {/* sr-only rather than hidden: display:none removes the input from
              the tab order, leaving no keyboard path to the file picker. */}
          <label className="soft-panel flex cursor-pointer items-center gap-3 px-5 py-4 text-sm font-bold text-[color:var(--color-ink)] focus-within:ring-2 focus-within:ring-[color:var(--color-tag)] dark:text-white">
            <ImagePlus size={18} />
            <span>{localPreview || previewUrl ? "Replace cover photo" : "Add cover photo"}</span>
            <input
              className="sr-only"
              type="file"
              accept="image/*"
              onChange={(event) => {
                const file = event.target.files?.[0] || null;
                if (file && rejectUnusableImage(file)) {
                  event.target.value = "";
                  return;
                }
                setForm((current) => ({ ...current, image: file }));
              }}
            />
          </label>
          {localPreview || previewUrl ? (
            <img
              src={localPreview || previewUrl}
              alt="Current listing cover"
              className="h-64 w-full rounded-[1.5rem] border border-[color:var(--color-line-strong)] object-cover"
            />
          ) : null}

          <label className="soft-panel flex cursor-pointer items-center gap-3 px-5 py-4 text-sm font-bold text-[color:var(--color-ink)] focus-within:ring-2 focus-within:ring-[color:var(--color-tag)] dark:text-white">
            <Images size={18} />
            <span>
              Add more photos
              <span className="ml-2 font-medium text-[color:var(--text-muted)]">
                ({galleryImages.length + extraFiles.length}/{MAX_EXTRA_PHOTOS})
              </span>
            </span>
            <input
              className="sr-only"
              type="file"
              accept="image/*"
              multiple
              onChange={(event) => {
                handleAddExtraPhotos(Array.from(event.target.files ?? []));
                event.target.value = "";
              }}
            />
          </label>

          {(galleryImages.length > 0 || extraFiles.length > 0) && (
            <div className="grid grid-cols-4 gap-3">
              {galleryImages.map((img) => (
                <div key={`saved-${img.id}`} className="relative aspect-square overflow-hidden rounded-[12px] border border-[color:var(--color-line-strong)]">
                  <img src={img.image_url} alt="" className="h-full w-full object-cover" />
                  <button
                    type="button"
                    onClick={() => handleRemoveGalleryImage(img.id)}
                    aria-label="Remove photo"
                    className="absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80"
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
              {extraFiles.map((file, i) => (
                <div key={`pending-${i}-${file.name}`} className="relative aspect-square overflow-hidden rounded-[12px] border border-dashed border-[color:var(--color-tag)]/40">
                  <img src={extraPreviews[i]} alt="" className="h-full w-full object-cover" />
                  <button
                    type="button"
                    onClick={() => setExtraFiles((current) => current.filter((_, idx) => idx !== i))}
                    aria-label="Remove pending photo"
                    className="absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80"
                  >
                    <X size={13} />
                  </button>
                  <span className="absolute bottom-1.5 left-1.5 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-semibold text-white">
                    New
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
        {error ? <p className="sm:col-span-2 text-sm font-bold text-[color:var(--color-urgent)]">{error}</p> : null}
          <button type="submit" disabled={submitting} className="primary-button sm:col-span-2">
            <ArrowRight size={15} />
            {submitting ? "Saving..." : editing ? "Save Changes" : "Create Listing"}
          </button>
        </form>
      </PageSection>
    </>
  );
}
