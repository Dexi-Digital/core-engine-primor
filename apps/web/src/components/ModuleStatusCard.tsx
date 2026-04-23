type Props = {
  title: string;
  scope: string[];
  backendPath: string;
};

export function ModuleStatusCard({ title, scope, backendPath }: Props) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <header className="flex items-start justify-between">
        <h1 className="text-2xl font-bold">{title}</h1>
        <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">
          Em desenvolvimento
        </span>
      </header>
      <p className="mt-2 text-sm text-slate-500">
        Endpoint backend: <code>{backendPath}</code>
      </p>
      <h2 className="mt-6 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Escopo deste módulo
      </h2>
      <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-slate-700">
        {scope.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </section>
  );
}
