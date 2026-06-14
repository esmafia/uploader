import { useQuery } from "@tanstack/react-query";
import { Videos } from "../api/client";

export default function VideosPage() {
  const { data, isLoading } = useQuery({ queryKey: ["videos"], queryFn: Videos.list });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Videos</h2>
        <p className="text-sm text-slate-500 mt-1">
          Files in <code>VideosDirPath/</code> on the shared volume.
        </p>
      </div>
      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left p-3">Name</th>
              <th className="text-left p-3">Size</th>
              <th className="text-left p-3">Modified</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && <tr><td className="p-4 text-slate-500" colSpan={3}>Loading…</td></tr>}
            {!isLoading && data && data.length === 0 && (
              <tr><td className="p-6 text-center text-slate-500" colSpan={3}>No videos.</td></tr>
            )}
            {data?.map(v => (
              <tr key={v.name}>
                <td className="p-3 font-medium">{v.name}</td>
                <td className="p-3 text-slate-500">{(v.size_bytes / 1024 / 1024).toFixed(1)} MB</td>
                <td className="p-3 text-slate-500">{new Date(v.modified_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
