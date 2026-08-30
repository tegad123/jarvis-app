/**
 * The two blurred radial fields the tokens specify per screen. Fixed, behind
 * everything, non-interactive.
 */
export default function AmbientField() {
  return (
    <div className="ambient-field" aria-hidden="true">
      <div className="ambient-blob ambient-blob-a h-[320px] w-[320px] -left-24 -top-16" />
      <div className="ambient-blob ambient-blob-b h-[280px] w-[280px] -right-20 top-1/3" />
    </div>
  )
}
