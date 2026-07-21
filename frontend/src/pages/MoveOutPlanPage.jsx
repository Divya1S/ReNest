import {
  ArrowRight,
  Camera,
  CheckCircle2,
  ClipboardCheck,
  Layers3,
  Plus,
  Sparkles,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import ImpactCard from "../components/ImpactCard";
import MoveOutTaskCard from "../components/MoveOutTaskCard";
import PageSection from "../components/PageSection";
import PublishReadinessPill from "../components/PublishReadinessPill";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime, formatLabel, formatMoney } from "../lib/formatters";

const filterOptions = [
  { value: "all", label: "All tasks" },
  { value: "open", label: "Open" },
  { value: "critical", label: "Critical" },
  { value: "done", label: "Done" },
];

const initialTaskForm = {
  title: "",
  details: "",
  category: "admin",
  due_at: "",
};

export default function MoveOutPlanPage() {
  usePageTitle("Move-Out Plan");
  const { sessionId } = useParams();
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("open");
  const [taskForm, setTaskForm] = useState(initialTaskForm);
  const [savingTask, setSavingTask] = useState(false);
  const [updatingTaskId, setUpdatingTaskId] = useState(null);
  const [confirmDeleteTask, setConfirmDeleteTask] = useState(null);

  async function loadPlan() {
    const data = await apiFetch(`/scan-sessions/${sessionId}/plan`);
    setPlan(data);
  }

  useEffect(() => {
    let active = true;

    loadPlan()
      .catch((requestError) => {
        if (active) {
          setError(requestError.message);
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const visibleTasks = useMemo(() => {
    const tasks = plan?.tasks || [];
    if (filter === "all") return tasks;
    if (filter === "done") return tasks.filter((task) => task.status === "done");
    if (filter === "critical") return tasks.filter((task) => ["overdue", "due_today"].includes(task.due_bucket));
    return tasks.filter((task) => task.status !== "done");
  }, [filter, plan]);

  async function handleCreateTask(event) {
    event.preventDefault();
    setSavingTask(true);
    try {
      await apiFetch(`/scan-sessions/${sessionId}/tasks`, {
        method: "POST",
        body: {
          title: taskForm.title,
          details: taskForm.details,
          category: taskForm.category,
          due_at: taskForm.due_at ? new Date(taskForm.due_at).toISOString() : null,
        },
      });
      setTaskForm(initialTaskForm);
      await loadPlan();
      toast.success("Task added to the move-out plan.");
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setSavingTask(false);
    }
  }

  async function handleStatusChange(task, nextStatus) {
    setUpdatingTaskId(task.id);
    try {
      await apiFetch(`/scan-tasks/${task.id}`, {
        method: "PATCH",
        body: { status: nextStatus },
      });
      await loadPlan();
      toast.success("Move-out task updated.");
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setUpdatingTaskId(null);
    }
  }

  async function handleDeleteTask(task) {
    setConfirmDeleteTask(null);
    setUpdatingTaskId(task.id);
    try {
      await apiFetch(`/scan-tasks/${task.id}`, { method: "DELETE" });
      await loadPlan();
      toast.success("Task removed.");
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setUpdatingTaskId(null);
    }
  }

  if (loading) {
    return <div className="paper-panel p-10 text-center text-lg font-bold">Loading move-out plan...</div>;
  }

  if (error || !plan) {
    return (
      <div className="paper-panel p-10 text-center text-lg font-bold text-[color:var(--color-urgent)]">
        {error || "Move-out plan not found."}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <UnsavedChangesDialog
        open={Boolean(confirmDeleteTask)}
        kicker="Confirm removal"
        title="Remove this custom task?"
        message="The task will be removed from your move-out plan. System-generated tasks will not be affected."
        confirmLabel="Remove task"
        cancelLabel="Keep task"
        onConfirm={() => handleDeleteTask(confirmDeleteTask)}
        onCancel={() => setConfirmDeleteTask(null)}
      />
      <PageSection className="paper-panel p-8 sm:p-10">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="sticker bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]">
              <ClipboardCheck size={14} />
              Move-Out Plan
            </div>
            <h1 className="mt-5 text-[34px] font-bold leading-[1.15] tracking-[-0.025em]">{plan.session.name}</h1>
            <p className="mt-4 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
              Track the operational side of move-out alongside publishing and pickups so nothing reusable or time-sensitive slips through at the end.
            </p>
            <p className="mt-4 text-sm font-semibold uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
              Deadline {plan.session.move_out_deadline ? formatDateTime(plan.session.move_out_deadline) : "not set"} • pickup zone {plan.session.pickup_zone || "not set"}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link to={`/scan/${plan.session.id}`} className="secondary-button">
              <Camera size={15} />
              Open Studio
            </Link>
            <Link to={`/clearout/${plan.session.id}`} className="secondary-button">
              <Layers3 size={15} />
              Clear-Out Board
            </Link>
            <Link to={`/publish/${plan.session.id}`} className="primary-button">
              <Sparkles size={15} />
              Publish Queue
            </Link>
          </div>
        </div>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-2 xl:grid-cols-4" delay={0.04}>
        <ImpactCard label="Plan complete" value={`${plan.task_summary.completion_percent}%`} tone="teal" icon={CheckCircle2} detail="How much of the room-level move-out plan is already done." />
        <ImpactCard label="Due today" value={plan.task_summary.due_today_tasks} tone="box" icon={ClipboardCheck} detail="Tasks that need attention in the next 24 hours." />
        <ImpactCard label="Overdue" value={plan.task_summary.overdue_tasks} tone="box" icon={ArrowRight} detail="Tasks that slipped past their intended due window." />
        <ImpactCard label="Publish ready" value={plan.summary.ready_to_publish_count} tone="blue" icon={Sparkles} detail="Draft items that can go live without more cleanup." />
      </PageSection>

      <PageSection className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]" delay={0.08}>
        <div className="space-y-6">
          <section className="soft-panel p-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <p className="label-title">Task queue</p>
                <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Run the move-out like a checklist, not a scramble.</h2>
              </div>
              <div className="flex flex-wrap gap-2">
                {filterOptions.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setFilter(option.value)}
                    className={`scan-chip transition ${filter === option.value ? "bg-[color:var(--color-tag)] text-white shadow-[0_12px_24px_rgba(11,122,114,0.18)]" : ""}`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-6 space-y-4">
              {visibleTasks.length ? (
                visibleTasks.map((task) => (
                  <MoveOutTaskCard
                    key={task.id}
                    task={task}
                    onStatusChange={handleStatusChange}
                    onDelete={(task) => setConfirmDeleteTask(task)}
                    disabled={updatingTaskId === task.id}
                  />
                ))
              ) : (
                <div className="rounded-3xl border border-dashed border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-5 py-10 text-center text-[color:var(--text-muted)]">
                  No tasks match this filter right now.
                </div>
              )}
            </div>
          </section>

          <section className="soft-panel p-6">
            <p className="label-title">Add a custom task</p>
            <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Capture the extra things this room still needs.</h2>
            <form onSubmit={handleCreateTask} className="mt-6 grid gap-4 sm:grid-cols-2">
              <input
                className="field sm:col-span-2"
                placeholder="Task title"
                value={taskForm.title}
                onChange={(event) => setTaskForm((current) => ({ ...current, title: event.target.value }))}
                required
              />
              <textarea
                className="field min-h-[8rem] sm:col-span-2"
                placeholder="Details or handoff notes"
                value={taskForm.details}
                onChange={(event) => setTaskForm((current) => ({ ...current, details: event.target.value }))}
              />
              <select
                className="field"
                value={taskForm.category}
                onChange={(event) => setTaskForm((current) => ({ ...current, category: event.target.value }))}
              >
                <option value="admin">Admin</option>
                <option value="setup">Setup</option>
                <option value="publish">Publish</option>
                <option value="donation">Donation</option>
                <option value="pickup">Pickup</option>
                <option value="room_reset">Room reset</option>
              </select>
              <input
                className="field"
                type="datetime-local"
                value={taskForm.due_at}
                onChange={(event) => setTaskForm((current) => ({ ...current, due_at: event.target.value }))}
              />
              <button type="submit" disabled={savingTask} className="primary-button sm:col-span-2">
                <Plus size={15} />
                {savingTask ? "Adding..." : "Add Task"}
              </button>
            </form>
          </section>
        </div>

        <div className="space-y-6">
          <section className="soft-panel p-6">
            <p className="label-title">Critical next actions</p>
            <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">What deserves attention before anything else.</h2>
            <div className="mt-6 space-y-4">
              {plan.critical_tasks.length ? (
                plan.critical_tasks.map((task) => (
                  <MoveOutTaskCard
                    key={task.id}
                    task={task}
                    compact
                    onStatusChange={handleStatusChange}
                    onDelete={(task) => setConfirmDeleteTask(task)}
                    disabled={updatingTaskId === task.id}
                  />
                ))
              ) : (
                <div className="rounded-3xl border border-dashed border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-5 py-8 text-center text-[color:var(--text-muted)]">
                  No critical tasks right now.
                </div>
              )}
            </div>
          </section>

          <section className="soft-panel p-6">
            <p className="label-title">Ready to publish</p>
            <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Drafts that can go live from this room.</h2>
            <div className="mt-6 space-y-4">
              {plan.ready_publish_items.length ? (
                plan.ready_publish_items.map((item) => (
                  <article key={item.id} className="paper-panel p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <PublishReadinessPill status={item.publish_readiness} />
                      <span className="scan-chip">{formatLabel(item.category)}</span>
                      <span className="scan-chip">{formatMoney(item.price_type, item.price_amount)}</span>
                    </div>
                    <h3 className="mt-3 text-[18px] font-semibold">{item.title}</h3>
                    <p className="mt-2 text-sm text-[color:var(--text-muted)]">{item.notes || "No draft notes yet."}</p>
                  </article>
                ))
              ) : (
                <div className="rounded-3xl border border-dashed border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-5 py-8 text-center text-[color:var(--text-muted)]">
                  No publish-ready drafts yet.
                </div>
              )}
            </div>
          </section>

          <section className="soft-panel p-6">
            <p className="label-title">Still blocked</p>
            <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Drafts missing enough info to stay off the market.</h2>
            <div className="mt-6 space-y-4">
              {plan.blocked_publish_items.length ? (
                plan.blocked_publish_items.map((item) => (
                  <article key={item.id} className="paper-panel p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <PublishReadinessPill status={item.publish_readiness} />
                      <span className="scan-chip">{formatLabel(item.category)}</span>
                    </div>
                    <h3 className="mt-3 text-[18px] font-semibold">{item.title}</h3>
                    <p className="mt-2 text-sm text-[color:var(--text-muted)]">
                      Missing {item.missing_fields.map((field) => formatLabel(field)).join(", ")}.
                    </p>
                  </article>
                ))
              ) : (
                <div className="rounded-3xl border border-dashed border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-5 py-8 text-center text-[color:var(--text-muted)]">
                  No blocked publish drafts right now.
                </div>
              )}
            </div>
          </section>
        </div>
      </PageSection>
    </div>
  );
}
