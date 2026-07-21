import { Save, SlidersHorizontal, Sparkles, X } from "lucide-react";
import React from "react";

const categoryOptions = [
  { value: "storage", label: "Storage" },
  { value: "lighting", label: "Lighting" },
  { value: "supplies", label: "School supplies" },
  { value: "comfort", label: "Comfort" },
  { value: "toiletries", label: "Toiletries" },
  { value: "decor", label: "Decor" },
  { value: "other", label: "Other" },
];

const conditionOptions = [
  { value: "new", label: "Like new" },
  { value: "good", label: "Good" },
  { value: "fair", label: "Fair" },
];

const priceTypeOptions = [
  { value: "free", label: "Free" },
  { value: "low_cost", label: "Low cost" },
];

const triageOptions = [
  { value: "review", label: "Review" },
  { value: "sell", label: "Sell / Post" },
  { value: "donate", label: "Donate" },
  { value: "keep", label: "Keep" },
  { value: "toss", label: "Toss" },
  { value: "done", label: "Done" },
];

function renderOptions(options) {
  return options.map((option) => (
    <option key={option.value} value={option.value}>
      {option.label}
    </option>
  ));
}

export default function BatchEditPanel({
  selectedCount,
  presets,
  form,
  setForm,
  saving,
  onApply,
  onClose,
  title = "Batch edit selected drafts",
  description = "Apply the same metadata to multiple drafts at once. Leave a field on No change when you do not want to touch it.",
}) {
  return (
    <section className="soft-panel p-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="sticker bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]">
            <SlidersHorizontal size={14} />
            Batch edit
          </div>
          <h2 className="mt-4 text-[24px] font-bold tracking-[-0.02em]">{title}</h2>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">{description}</p>
          <p className="mt-3 text-sm font-semibold text-slate-500">
            {selectedCount} draft{selectedCount === 1 ? "" : "s"} selected
          </p>
        </div>
        {onClose ? (
          <button type="button" onClick={onClose} className="secondary-button self-start">
            <X size={15} />
            Close
          </button>
        ) : null}
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <label className="space-y-2">
          <span className="label-title">Quick preset</span>
          <select
            className="field"
            value={form.preset_key}
            onChange={(event) => setForm((current) => ({ ...current, preset_key: event.target.value }))}
          >
            <option value="">No change</option>
            {presets.map((preset) => (
              <option key={preset.key} value={preset.key}>
                {preset.label}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-2">
          <span className="label-title">Category</span>
          <select
            className="field"
            value={form.category}
            onChange={(event) => setForm((current) => ({ ...current, category: event.target.value }))}
          >
            <option value="">No change</option>
            {renderOptions(categoryOptions)}
          </select>
        </label>

        <label className="space-y-2">
          <span className="label-title">Condition</span>
          <select
            className="field"
            value={form.condition}
            onChange={(event) => setForm((current) => ({ ...current, condition: event.target.value }))}
          >
            <option value="">No change</option>
            {renderOptions(conditionOptions)}
          </select>
        </label>

        <label className="space-y-2">
          <span className="label-title">Price type</span>
          <select
            className="field"
            value={form.price_type}
            onChange={(event) => setForm((current) => ({ ...current, price_type: event.target.value }))}
          >
            <option value="">No change</option>
            {renderOptions(priceTypeOptions)}
          </select>
        </label>

        <label className="space-y-2">
          <span className="label-title">Price amount</span>
          <input
            className="field"
            type="number"
            min="0"
            step="0.01"
            value={form.price_amount}
            onChange={(event) => setForm((current) => ({ ...current, price_amount: event.target.value }))}
            placeholder="No change"
          />
        </label>

        <label className="space-y-2">
          <span className="label-title">Estimated retail value</span>
          <input
            className="field"
            type="number"
            min="0"
            step="0.01"
            value={form.estimated_retail_value}
            onChange={(event) =>
              setForm((current) => ({ ...current, estimated_retail_value: event.target.value }))
            }
            placeholder="No change"
          />
        </label>

        <label className="space-y-2">
          <span className="label-title">Triage status</span>
          <select
            className="field"
            value={form.triage_status}
            onChange={(event) => setForm((current) => ({ ...current, triage_status: event.target.value }))}
          >
            <option value="">No change</option>
            {renderOptions(triageOptions)}
          </select>
        </label>

        <label className="space-y-2">
          <span className="label-title">Pickup zone</span>
          <input
            className="field"
            value={form.pickup_zone}
            onChange={(event) => setForm((current) => ({ ...current, pickup_zone: event.target.value }))}
            placeholder="No change"
          />
        </label>

        <label className="space-y-2">
          <span className="label-title">Move-out deadline</span>
          <input
            className="field"
            type="datetime-local"
            value={form.move_out_deadline}
            onChange={(event) =>
              setForm((current) => ({ ...current, move_out_deadline: event.target.value }))
            }
          />
        </label>
      </div>

      <div className="mt-6 flex flex-wrap gap-3">
        <button type="button" onClick={onApply} disabled={saving} className="primary-button">
          <Save size={15} />
          {saving ? "Applying..." : "Apply batch edits"}
        </button>
        <div className="ghost-button">
          <Sparkles size={15} />
          Presets fill title, category, price mode, retail value, and notes.
        </div>
      </div>
    </section>
  );
}
