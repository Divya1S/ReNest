import {
  CheckCircle2,
  HandHelping,
  LifeBuoy,
  MapPin,
  Search,
  Sparkles,
  XCircle,
} from "lucide-react";
import React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import DraftStatusNotice from "../components/DraftStatusNotice";
import PageSection from "../components/PageSection";
import SkeletonLoader from "../components/SkeletonLoader";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { useAuth } from "../context/AuthContext";
import { useDebounce } from "../hooks/useDebounce";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePersistentDraftState } from "../hooks/usePersistentDraftState";
import { useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { apiFetch, asResults, toLocalDateTimeInput } from "../lib/api";
import { formatCurrencyValue, formatDateTime, formatLabel } from "../lib/formatters";

const CATEGORIES = [
  { label: "All needs", value: "" },
  { label: "Storage", value: "storage" },
  { label: "Lighting", value: "lighting" },
  { label: "Toiletries", value: "toiletries" },
  { label: "Comfort", value: "comfort" },
  { label: "Supplies", value: "supplies" },
  { label: "Decor", value: "decor" },
  { label: "Other", value: "other" },
];

const STATUS_TONE = {
  open: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300",
  matched: "bg-blue-100 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300",
  fulfilled: "bg-slate-900 text-white dark:bg-white dark:text-slate-900",
  closed: "bg-slate-100 text-[color:var(--text-muted)] dark:bg-slate-800 dark:text-slate-300",
};

function RequestCard({
  request,
  isOwner,
  matchingListings,
  selectedListingId,
  onSelectListing,
  onMatch,
  onStatusChange,
  matchingBusy,
  statusBusy,
}) {
  return (
    <article className="bulletin-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-3 py-1 text-[0.65rem] font-black uppercase tracking-[0.14em] ${STATUS_TONE[request.status] || STATUS_TONE.open}`}>
          {formatLabel(request.status)}
        </span>
        <span className="scan-chip">{formatLabel(request.category)}</span>
        <span className="scan-chip">{request.time_left_label}</span>
      </div>

      <div className="mt-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">{request.title}</h3>
          <p className="mt-2 text-sm text-[color:var(--text-muted)]">
            {isOwner ? "Posted by you" : `Requested by ${request.seeker.display_name}`}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Budget</p>
          <p className="mt-1 text-[16px] font-semibold text-[color:var(--color-tag)]">
            {request.budget_amount > 0 ? formatCurrencyValue(request.budget_amount) : "Free preferred"}
          </p>
        </div>
      </div>

      {request.description ? (
        <p className="mt-4 text-sm leading-6 text-[color:var(--text-muted)]">{request.description}</p>
      ) : null}

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl bg-slate-50 px-4 py-4 dark:bg-slate-800/50">
          <p className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Pickup zone</p>
          <p className="mt-2 flex items-center gap-2 text-sm font-black text-[color:var(--color-ink)] dark:text-white">
            <MapPin size={15} />
            {request.pickup_zone}
          </p>
        </div>
        <div className="rounded-2xl bg-slate-50 px-4 py-4 dark:bg-slate-800/50">
          <p className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Needed by</p>
          <p className="mt-2 text-sm font-black text-[color:var(--color-ink)] dark:text-white">{formatDateTime(request.needed_by)}</p>
        </div>
      </div>

      {request.matched_listing_detail ? (
        <div className="mt-5 rounded-[1.4rem] border border-[color:var(--color-line)] bg-white px-4 py-4 dark:bg-slate-900/50">
          <p className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Matched listing</p>
          <div className="mt-3 flex items-center justify-between gap-4">
            <div>
              <p className="text-base font-black text-[color:var(--color-ink)] dark:text-white">{request.matched_listing_detail.title}</p>
              <p className="mt-1 text-sm text-[color:var(--text-muted)]">
                {request.matched_listing_detail.owner.display_name} • {request.matched_listing_detail.pickup_zone}
              </p>
            </div>
            <Link to={`/listings/${request.matched_listing_detail.id}`} className="ghost-button">
              Open Listing
            </Link>
          </div>
        </div>
      ) : null}

      {isOwner ? (
        <div className="mt-5 flex flex-wrap gap-3">
          {request.status === "matched" ? (
            <>
              <button
                type="button"
                onClick={() => onStatusChange(request.id, "fulfilled")}
                disabled={statusBusy}
                className="primary-button"
              >
                <CheckCircle2 size={16} />
                {statusBusy ? "Saving..." : "Mark Fulfilled"}
              </button>
              <button
                type="button"
                onClick={() => onStatusChange(request.id, "open")}
                disabled={statusBusy}
                className="secondary-button"
              >
                Reopen
              </button>
            </>
          ) : request.status === "open" ? (
            <button
              type="button"
              onClick={() => onStatusChange(request.id, "closed")}
              disabled={statusBusy}
              className="secondary-button"
            >
              <XCircle size={16} />
              Close Request
            </button>
          ) : null}
        </div>
      ) : request.status === "open" ? (
        <div className="mt-5 space-y-3">
          {matchingListings.length ? (
            <>
              <select
                value={selectedListingId || ""}
                onChange={(event) => onSelectListing(request.id, event.target.value)}
                className="field"
              >
                <option value="">Match one of your active listings</option>
                {matchingListings.map((listing) => (
                  <option key={listing.id} value={listing.id}>
                    {listing.title}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => onMatch(request.id)}
                disabled={!selectedListingId || matchingBusy}
                className="primary-button w-full"
              >
                <HandHelping size={16} />
                {matchingBusy ? "Matching..." : "Offer Matching Listing"}
              </button>
            </>
          ) : (
            <div className="rounded-[1.3rem] bg-[color:var(--color-teal-soft)] px-4 py-4 text-sm leading-6 text-[color:var(--text-muted)]">
              No active listing in this category yet. Create one, then come back to match it.
            </div>
          )}
        </div>
      ) : null}
    </article>
  );
}

export default function RequestsPage() {
  usePageTitle("Requests");
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [isFilterPending, startFilterTransition] = React.useTransition();
  const [requests, setRequests] = React.useState([]);
  const [matchCenter, setMatchCenter] = React.useState({
    stats: {
      fulfillable_needs: 0,
      requests_with_matches: 0,
      campus_urgent_requests: 0,
    },
    fulfill_opportunities: [],
    request_recommendations: [],
    urgent_requests: [],
  });
  const [myListings, setMyListings] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [showCreate, setShowCreate] = React.useState(false);
  const [matchSelections, setMatchSelections] = React.useState({});
  const [matchBusyId, setMatchBusyId] = React.useState(null);
  const [statusBusyId, setStatusBusyId] = React.useState(null);
  const [category, setCategory] = React.useState(() => searchParams.get("category") || "");
  const [searchInput, setSearchInput] = React.useState(() => searchParams.get("search") || "");
  const deferredSearchInput = React.useDeferredValue(searchInput);
  const search = useDebounce(deferredSearchInput, 250);
  const defaultRequestCategory = searchParams.get("category") || "storage";
  const initialRequestForm = React.useMemo(() => ({
    title: "",
    description: "",
    category: defaultRequestCategory,
    pickup_zone: user?.campus_name ? `${user.campus_name} pickup zone` : "",
    needed_by: toLocalDateTimeInput(new Date(Date.now() + 48 * 60 * 60 * 1000)),
    budget_amount: "0",
    urgency: "soon",
  }), [defaultRequestCategory, user?.campus_name]);
  const requestDraft = usePersistentDraftState({
    key: "rescue-request-create",
    initialValue: initialRequestForm,
  });
  const { state: form, setState: setForm } = requestDraft;
  const unsavedGuard = useUnsavedChangesGuard({
    when: showCreate && requestDraft.isDirty,
    title: "Leave before posting this request?",
    message:
      "Your rescue request is still in progress. The form is autosaved locally, but leaving now will interrupt posting it to the campus board.",
  });

  React.useEffect(() => {
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (search) params.set("search", search);
    setSearchParams(params, { replace: true });
  }, [category, search, setSearchParams]);

  const loadData = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (category) params.set("category", category);
      if (search) params.set("search", search);
      const query = params.toString() ? `?${params.toString()}` : "";
      const [requestData, listingData, matchData] = await Promise.all([
        apiFetch(`/requests${query}`),
        apiFetch("/listings?mine=1&status=active"),
        apiFetch("/match-center"),
      ]);
      setRequests(asResults(requestData));
      setMyListings(asResults(listingData));
      setMatchCenter(matchData);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }, [category, search]);

  React.useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleCreateRequest(event) {
    event.preventDefault();
    try {
      await apiFetch("/requests", {
        method: "POST",
        body: {
          ...form,
          needed_by: new Date(form.needed_by).toISOString(),
        },
      });
      toast.success("Request posted to the campus board.");
      requestDraft.clearDraft({ reset: true });
      setShowCreate(false);
      await loadData();
    } catch (requestError) {
      toast.error(requestError.message);
    }
  }

  async function handleMatch(requestId) {
    const listingId = matchSelections[requestId];
    if (!listingId) return;
    setMatchBusyId(requestId);
    try {
      await apiFetch(`/requests/${requestId}/match`, {
        method: "POST",
        body: { listing: Number(listingId) },
      });
      toast.success("Request matched to your listing.");
      await loadData();
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setMatchBusyId(null);
    }
  }

  async function handleStatusChange(requestId, status) {
    setStatusBusyId(requestId);
    try {
      await apiFetch(`/requests/${requestId}`, {
        method: "PATCH",
        body: { status },
      });
      toast.success(`Request ${formatLabel(status)}.`);
      await loadData();
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setStatusBusyId(null);
    }
  }

  const myRequests = requests.filter((request) => request.seeker.id === user?.id);
  const campusRequests = requests.filter((request) => request.seeker.id !== user?.id);
  const matchedForMe = myRequests.filter((request) => request.status === "matched").length;
  const openCampusCount = campusRequests.filter((request) => request.status === "open").length;

  if (loading) {
    return (
      <div className="space-y-8">
        <SkeletonLoader className="paper-panel h-44" />
        <div className="grid gap-4 md:grid-cols-3">
          {[1, 2, 3].map((item) => (
            <SkeletonLoader key={item} className="h-28 !rounded-[1.8rem]" />
          ))}
        </div>
        <div className="grid gap-8 xl:grid-cols-2">
          <SkeletonLoader className="h-[28rem] !rounded-[2rem]" />
          <SkeletonLoader className="h-[28rem] !rounded-[2rem]" />
        </div>
      </div>
    );
  }

  return (
    <>
      <UnsavedChangesDialog {...unsavedGuard.dialogProps} />
      <div className="space-y-8 pb-20">
      <PageSection className="paper-panel p-8 sm:p-10">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="sticker bg-[color:var(--color-tag)] text-white">
              <LifeBuoy size={14} />
              Rescue Requests
            </div>
            <h1 className="mt-5 text-[34px] font-bold leading-[1.15] tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
              Turn campus need into a rescue match.
            </h1>
            <p className="mt-4 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
              Students can post what they still need before move-in, and rescuers can match those needs with items that would otherwise be thrown away.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <button type="button" onClick={() => setShowCreate((value) => !value)} className="primary-button">
              <Sparkles size={15} />
              {showCreate ? "Hide request form" : "Post a need"}
            </button>
            <Link to="/browse" className="secondary-button">
              <Search size={15} />
              Browse listings
            </Link>
          </div>
        </div>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-3" delay={0.04}>
        <div className="paper-panel p-5">
          <p className="label-title">Open campus needs</p>
          <p className="metric-number mt-3">{openCampusCount}</p>
          <p className="mt-4 text-sm text-[color:var(--text-muted)]">Requests you can help fulfill right now.</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Your active requests</p>
          <p className="metric-number mt-3">{myRequests.filter((request) => ["open", "matched"].includes(request.status)).length}</p>
          <p className="mt-4 text-sm text-[color:var(--text-muted)]">Needs you are still waiting to solve.</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Matched for you</p>
          <p className="metric-number mt-3">{matchedForMe}</p>
          <p className="mt-4 text-sm text-[color:var(--text-muted)]">Requests already linked to a real rescue item.</p>
        </div>
      </PageSection>

      <PageSection className="grid gap-8 xl:grid-cols-2" delay={0.06}>
        <section className="paper-panel p-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="label-title">Smart matches</p>
              <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Requests your listings can fulfill</h2>
            </div>
            <span className="scan-chip">{matchCenter.stats.fulfillable_needs} live</span>
          </div>
          <div className="mt-6 space-y-4">
            {matchCenter.fulfill_opportunities.length ? (
              matchCenter.fulfill_opportunities.map((entry) => (
                <div key={entry.listing.id} className="paper-panel p-4 !rounded-2xl">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-[16px] font-semibold text-[color:var(--color-ink)] dark:text-white">{entry.listing.title}</p>
                      <p className="mt-1 text-sm text-[color:var(--text-muted)]">
                        {entry.match_count} request{entry.match_count === 1 ? "" : "s"} in {formatLabel(entry.listing.category)}
                      </p>
                    </div>
                    <Link to={`/listings/${entry.listing.id}`} className="ghost-button">
                      Open Listing
                    </Link>
                  </div>
                  <div className="mt-4 space-y-3">
                    {entry.matches.map((match) => (
                      <div key={match.id} className="rounded-[1.2rem] border border-[color:var(--color-line)] bg-white px-4 py-4 dark:bg-slate-900/50">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-sm font-black text-[color:var(--color-ink)] dark:text-white">{match.title}</p>
                          <span className="scan-chip">{match.time_left_label}</span>
                        </div>
                        <p className="mt-2 text-sm text-[color:var(--text-muted)]">
                          {match.seeker.display_name} • {match.pickup_zone}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <div className="paper-panel p-8 text-[color:var(--text-muted)]">
                No smart-match opportunities yet. Once campus needs line up with your active listings, they will appear here.
              </div>
            )}
          </div>
        </section>

        <section className="paper-panel p-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="label-title">For you</p>
              <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Your requests with live matches</h2>
            </div>
            <span className="scan-chip">{matchCenter.stats.requests_with_matches} ready</span>
          </div>
          <div className="mt-6 space-y-4">
            {matchCenter.request_recommendations.length ? (
              matchCenter.request_recommendations.map((entry) => (
                <div key={entry.request.id} className="paper-panel p-4 !rounded-2xl">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-[16px] font-semibold text-[color:var(--color-ink)] dark:text-white">{entry.request.title}</p>
                      <p className="mt-1 text-sm text-[color:var(--text-muted)]">
                        {entry.match_count} listing{entry.match_count === 1 ? "" : "s"} ready to claim
                      </p>
                    </div>
                    <span className="scan-chip">{entry.request.time_left_label}</span>
                  </div>
                  <div className="mt-4 space-y-3">
                    {entry.matches.map((match) => (
                      <div key={match.id} className="rounded-[1.2rem] border border-[color:var(--color-line)] bg-white px-4 py-4 dark:bg-slate-900/50">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-sm font-black text-[color:var(--color-ink)] dark:text-white">{match.title}</p>
                          <Link to={`/listings/${match.id}`} className="ghost-button">
                            Open
                          </Link>
                        </div>
                        <p className="mt-2 text-sm text-[color:var(--text-muted)]">
                          {match.owner.display_name} • {match.pickup_zone}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <div className="paper-panel p-8 text-[color:var(--text-muted)]">
                Your open requests do not have live matches yet. Post clearer details or check back after more rescuers list items.
              </div>
            )}
          </div>
        </section>
      </PageSection>

      {showCreate ? (
        <PageSection className="paper-panel p-8" delay={0.06}>
          <p className="label-title">Create a rescue request</p>
          <h2 className="mt-2 text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Post what you still need.</h2>
          <form onSubmit={handleCreateRequest} className="mt-6 grid gap-4 md:grid-cols-2">
            <div className="md:col-span-2">
              <DraftStatusNotice
                lastSavedAt={requestDraft.lastSavedAt}
                hasRestoredDraft={requestDraft.hasRestoredDraft}
                onDiscard={() => requestDraft.clearDraft({ reset: true })}
              />
            </div>
            <label className="block">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Title</span>
              <input
                value={form.title}
                onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
                className="field"
                placeholder="Need a desk lamp"
                required
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Category</span>
              <select
                value={form.category}
                onChange={(event) => setForm((current) => ({ ...current, category: event.target.value }))}
                className="field"
              >
                {CATEGORIES.filter((item) => item.value).map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </label>
            <label className="block md:col-span-2">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Details</span>
              <textarea
                value={form.description}
                onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                className="field min-h-[130px]"
                placeholder="Share what size, condition, or move-in use case would help."
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Pickup zone</span>
              <input
                value={form.pickup_zone}
                onChange={(event) => setForm((current) => ({ ...current, pickup_zone: event.target.value }))}
                className="field"
                required
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Needed by</span>
              <input
                type="datetime-local"
                value={form.needed_by}
                onChange={(event) => setForm((current) => ({ ...current, needed_by: event.target.value }))}
                className="field"
                required
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Budget</span>
              <input
                type="number"
                min="0"
                step="1"
                value={form.budget_amount}
                onChange={(event) => setForm((current) => ({ ...current, budget_amount: event.target.value }))}
                className="field"
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Urgency</span>
              <select
                value={form.urgency}
                onChange={(event) => setForm((current) => ({ ...current, urgency: event.target.value }))}
                className="field"
              >
                <option value="flexible">Flexible</option>
                <option value="soon">Soon</option>
                <option value="urgent">Urgent</option>
              </select>
            </label>
            <div className="md:col-span-2 flex flex-wrap gap-3">
              <button type="submit" className="primary-button">
                <Sparkles size={15} />
                Post request
              </button>
              <button type="button" onClick={() => setShowCreate(false)} className="secondary-button">
                Cancel
              </button>
            </div>
          </form>
        </PageSection>
      ) : null}

      <PageSection className="paper-panel p-6" delay={0.08}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {CATEGORIES.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => startFilterTransition(() => setCategory(item.value))}
                className={`rounded-full px-4 py-2 text-xs font-black uppercase tracking-[0.12em] transition ${
                  category === item.value
                    ? "bg-[color:var(--color-tag)] text-white"
                    : "bg-slate-100 text-[color:var(--text-muted)] hover:text-[color:var(--color-tag)] dark:bg-slate-800 dark:text-slate-300"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 rounded-full border border-[color:var(--color-line)] bg-white px-4 py-3 dark:bg-slate-900">
            <Search size={16} className="text-[color:var(--text-muted)]" />
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              className="w-full bg-transparent text-sm font-medium outline-none"
              placeholder="Search requests"
            />
          </div>
        </div>
        {isFilterPending ? (
          <p className="mt-4 text-xs font-bold uppercase tracking-[0.16em] text-[color:var(--text-muted)]">
            Refreshing request filters…
          </p>
        ) : null}
      </PageSection>

      {error ? (
        <div className="paper-panel p-10 text-center text-lg font-bold text-[color:var(--color-urgent)]">
          {error}
        </div>
      ) : null}

      <PageSection className="grid gap-8 xl:grid-cols-2" delay={0.12}>
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Campus request board</h2>
            <span className="scan-chip">{campusRequests.length} live</span>
          </div>
          {campusRequests.length ? (
            campusRequests.map((request) => {
              const matchingListings = myListings.filter((listing) => listing.category === request.category);
              return (
                <RequestCard
                  key={request.id}
                  request={request}
                  isOwner={false}
                  matchingListings={matchingListings}
                  selectedListingId={matchSelections[request.id]}
                  onSelectListing={(requestId, listingId) =>
                    setMatchSelections((current) => ({ ...current, [requestId]: listingId }))
                  }
                  onMatch={handleMatch}
                  onStatusChange={handleStatusChange}
                  matchingBusy={matchBusyId === request.id}
                  statusBusy={false}
                />
              );
            })
          ) : (
            <div className="paper-panel p-8 text-[color:var(--text-muted)]">
              No matching campus requests right now. Check back after more students post what they need.
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Your requests</h2>
            <Link to="/dashboard" className="ghost-button">
              Back to dashboard
            </Link>
          </div>
          {myRequests.length ? (
            myRequests.map((request) => (
              <RequestCard
                key={request.id}
                request={request}
                isOwner
                matchingListings={[]}
                selectedListingId=""
                onSelectListing={() => null}
                onMatch={handleMatch}
                onStatusChange={handleStatusChange}
                matchingBusy={false}
                statusBusy={statusBusyId === request.id}
              />
            ))
          ) : (
            <div className="paper-panel p-8 text-[color:var(--text-muted)]">
              You have not posted any rescue requests yet.
            </div>
          )}
        </div>
      </PageSection>
      </div>
    </>
  );
}
