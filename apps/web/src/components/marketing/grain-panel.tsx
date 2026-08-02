/**
 * The grain-gradient panel behind the auth pages.
 *
 * The look this is after is a WebGL grain shader, but it is two stacked
 * gradients with a turbulence tile over them instead: no new dependency, no
 * canvas, no GPU context sitting on the sign-in page, and it renders on the
 * server. The dithered edge that sells the effect comes from `mix-blend-overlay`
 * on the noise rather than from the gradient itself, which is why the colour
 * stops can stay soft.
 *
 * Jasmine rather than the orange it is modelled on: the sign-in page is the
 * first surface a stranger sees, and it should be wearing the product's own
 * colour.
 *
 * No client boundary here on purpose. The drift is a CSS animation, so this
 * stays a server component and ships no JavaScript.
 */
export function GrainPanel({
  children,
  className = "",
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`relative isolate overflow-hidden bg-[#050505] ${className}`}>
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-20"
        style={{
          background: [
            "radial-gradient(115% 85% at 100% 6%, #FFE787 0%, #F8D64F 24%, rgba(248,214,79,0) 60%)",
            "radial-gradient(95% 72% at -12% 104%, #F2C864 0%, rgba(242,200,100,0) 56%)",
            "#050505",
          ].join(", "),
        }}
      />
      {/* Oversized so the drift never exposes an edge. */}
      <div
        aria-hidden="true"
        className="grain-noise animate-grain-drift absolute -inset-[18%] -z-10 opacity-[0.45] mix-blend-overlay"
      />
      {children}
    </div>
  );
}
