import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useDropzone } from "react-dropzone";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Accounts, Schedules, Uploads } from "../api/client";
import clsx from "clsx";

const ytRe =
  /^https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)[\w-]+/i;

const schema = z
  .object({
    username: z.string().min(1, "Select an account"),
    title: z.string().min(1, "Required").max(2200, "Max 2200 chars"),
    source_type: z.enum(["local", "youtube"]),
    youtube_url: z.string().optional(),
    scheduled_for: z.string().optional(), // datetime-local
    // Options
    allow_comment: z.number().default(1),
    allow_duet: z.number().default(0),
    allow_stitch: z.number().default(0),
    visibility_type: z.number().default(0),
  })
  .superRefine((val, ctx) => {
    if (val.source_type === "youtube") {
      if (!val.youtube_url) {
        ctx.addIssue({ code: "custom", path: ["youtube_url"], message: "Required" });
      } else if (!ytRe.test(val.youtube_url)) {
        ctx.addIssue({ code: "custom", path: ["youtube_url"], message: "Not a valid YouTube URL" });
      }
    }
    if (val.scheduled_for) {
      const when = new Date(val.scheduled_for);
      if (isNaN(when.getTime()) || when.getTime() <= Date.now()) {
        ctx.addIssue({ code: "custom", path: ["scheduled_for"], message: "Must be in the future" });
      }
    }
    if (val.scheduled_for && val.visibility_type === 1) {
      ctx.addIssue({
        code: "custom",
        path: ["visibility_type"],
        message: "Private videos cannot be scheduled",
      });
    }
  });

type FormValues = z.infer<typeof schema>;

export default function UploadPage() {
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: Accounts.list });
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      source_type: "local",
      allow_comment: 1,
      allow_duet: 0,
      allow_stitch: 0,
      visibility_type: 0,
    },
  });
  const sourceType = form.watch("source_type");

  const dz = useDropzone({
    accept: { "video/mp4": [".mp4"], "video/webm": [".webm"] },
    maxFiles: 1,
    onDrop: files => {
      setFileError(null);
      setFile(files[0] ?? null);
    },
  });

  const scheduleMut = useMutation({ mutationFn: Schedules.create });
  const uploadFileMut = useMutation({ mutationFn: Uploads.file });
  const uploadYtMut = useMutation({ mutationFn: Uploads.youtube });

  const isBusy = scheduleMut.isPending || uploadFileMut.isPending || uploadYtMut.isPending;

  const optionsPayload = (values: FormValues) => ({
    allow_comment: values.allow_comment,
    allow_duet: values.allow_duet,
    allow_stitch: values.allow_stitch,
    visibility_type: values.visibility_type,
  });

  const onSubmit = async (values: FormValues) => {
    setMessage(null);

    // Local + no file = error not covered by zod (file state is outside form).
    if (values.source_type === "local" && !file) {
      setFileError("Drop a video file or switch to YouTube URL");
      return;
    }

    try {
      if (values.scheduled_for) {
        const whenIso = new Date(values.scheduled_for).toISOString();
        // Local files can only be scheduled if they already live on the shared
        // volume. The drag-and-drop flow here is for *immediate* uploads.
        if (values.source_type === "local") {
          setMessage(
            "Scheduled uploads with a drag-and-drop file aren't supported — " +
              "drop the file into VideosDirPath first, or schedule a YouTube URL.",
          );
          return;
        }
        const r = await scheduleMut.mutateAsync({
          username: values.username,
          title: values.title,
          source_type: "youtube",
          source_ref: values.youtube_url!,
          scheduled_for: whenIso,
          options: optionsPayload(values),
        });
        setMessage(`Scheduled — job #${r.id} at ${new Date(r.scheduled_for).toLocaleString()}`);
      } else if (values.source_type === "local") {
        const fd = new FormData();
        fd.append("video", file!);
        fd.append("username", values.username);
        fd.append("title", values.title);
        fd.append("options_json", JSON.stringify(optionsPayload(values)));
        const r = await uploadFileMut.mutateAsync(fd);
        setMessage(r.message);
      } else {
        const r = await uploadYtMut.mutateAsync({
          username: values.username,
          title: values.title,
          youtube_url: values.youtube_url,
          options: optionsPayload(values),
        });
        setMessage(r.message);
      }
    } catch (e: any) {
      setMessage(`Error: ${e?.response?.data?.detail ?? e.message}`);
    }
  };

  const accountOptions = useMemo(
    () => (accounts ?? []).filter(a => a.has_valid_session),
    [accounts],
  );

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Upload</h2>
        <p className="text-sm text-slate-500 mt-1">
          Upload now, or set a future time to hand the job to the scheduler.
        </p>
      </div>

      <form onSubmit={form.handleSubmit(onSubmit)} className="card space-y-5">
        {/* Account */}
        <div>
          <label className="label">Account</label>
          <select className="input" {...form.register("username")}>
            <option value="">Select an account…</option>
            {accountOptions.map(a => (
              <option key={a.id} value={a.username}>
                {a.username}
              </option>
            ))}
          </select>
          {form.formState.errors.username && (
            <p className="text-xs text-red-600 mt-1">{form.formState.errors.username.message}</p>
          )}
        </div>

        {/* Source toggle */}
        <div className="flex gap-2 rounded-md bg-slate-100 p-1 w-fit">
          {(["local", "youtube"] as const).map(t => (
            <button
              key={t}
              type="button"
              className={clsx(
                "px-3 py-1.5 text-sm font-medium rounded",
                sourceType === t ? "bg-white shadow-sm" : "text-slate-500",
              )}
              onClick={() => form.setValue("source_type", t)}
            >
              {t === "local" ? "Local file" : "YouTube URL"}
            </button>
          ))}
        </div>

        {sourceType === "local" ? (
          <div>
            <label className="label">Video</label>
            <div
              {...dz.getRootProps()}
              className={clsx(
                "rounded-md border-2 border-dashed p-8 text-center cursor-pointer",
                dz.isDragActive ? "border-brand-500 bg-brand-50" : "border-slate-200",
              )}
            >
              <input {...dz.getInputProps()} />
              {file ? (
                <p className="text-sm">
                  <span className="font-medium">{file.name}</span>{" "}
                  <span className="text-slate-500">
                    ({(file.size / 1024 / 1024).toFixed(1)} MB)
                  </span>
                </p>
              ) : (
                <p className="text-sm text-slate-500">
                  Drop an .mp4 or .webm here, or click to pick.
                </p>
              )}
            </div>
            {fileError && <p className="text-xs text-red-600 mt-1">{fileError}</p>}
          </div>
        ) : (
          <div>
            <label className="label">YouTube URL</label>
            <input
              className="input"
              placeholder="https://www.youtube.com/watch?v=…"
              {...form.register("youtube_url")}
            />
            {form.formState.errors.youtube_url && (
              <p className="text-xs text-red-600 mt-1">
                {form.formState.errors.youtube_url.message}
              </p>
            )}
          </div>
        )}

        <div>
          <label className="label">Caption</label>
          <textarea className="input h-20" {...form.register("title")} />
          {form.formState.errors.title && (
            <p className="text-xs text-red-600 mt-1">{form.formState.errors.title.message}</p>
          )}
        </div>

        <details className="border-t pt-4">
          <summary className="text-sm font-medium text-slate-600 cursor-pointer">
            Advanced options
          </summary>
          <div className="grid grid-cols-2 gap-4 mt-4">
            {(
              [
                ["allow_comment", "Allow comments"],
                ["allow_duet", "Allow duet"],
                ["allow_stitch", "Allow stitch"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  {...form.register(key, { setValueAs: v => (v ? 1 : 0) })}
                />
                {label}
              </label>
            ))}
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                {...form.register("visibility_type", { setValueAs: v => (v ? 1 : 0) })}
              />
              Private
            </label>
          </div>
          {form.formState.errors.visibility_type && (
            <p className="text-xs text-red-600 mt-2">
              {form.formState.errors.visibility_type.message as string}
            </p>
          )}
        </details>

        <div>
          <label className="label">Schedule for (optional)</label>
          <input
            type="datetime-local"
            className="input w-fit"
            {...form.register("scheduled_for")}
          />
          {form.formState.errors.scheduled_for && (
            <p className="text-xs text-red-600 mt-1">
              {form.formState.errors.scheduled_for.message}
            </p>
          )}
          <p className="text-xs text-slate-500 mt-1">
            Leave blank to upload immediately.
          </p>
        </div>

        <div className="flex items-center justify-between pt-2">
          <div className="text-sm text-slate-600">{message}</div>
          <button className="btn-primary" disabled={isBusy}>
            {isBusy ? "Working…" : form.watch("scheduled_for") ? "Schedule" : "Upload now"}
          </button>
        </div>
      </form>
    </div>
  );
}
