export default function DashboardLoading() {
  return (
    <div className="mx-auto min-h-full w-full max-w-[1600px] px-4 pb-24 pt-5 sm:px-6 lg:px-8 lg:pb-10">
      <div className="h-3 w-44 animate-pulse rounded-full bg-white/[0.06]" />
      <div className="mt-5 h-14 max-w-2xl animate-pulse rounded-2xl bg-white/[0.055]" />
      <div className="mt-4 h-4 max-w-lg animate-pulse rounded-full bg-white/[0.04]" />
      <div className="mt-8 grid grid-cols-1 gap-3 xl:grid-cols-12">
        <div className="h-[360px] animate-pulse rounded-[22px] border border-white/[0.05] bg-white/[0.025] xl:col-span-8" />
        <div className="grid grid-cols-2 gap-3 xl:col-span-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div
              key={index}
              className="h-36 animate-pulse rounded-[20px] border border-white/[0.05] bg-white/[0.025]"
            />
          ))}
        </div>
      </div>
    </div>
  );
}
