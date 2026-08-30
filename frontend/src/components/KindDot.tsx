import { DOT_BG, type KindSemantic } from '../lib/kinds'

/**
 * The kind dot. Per the tokens these are the one place fills are allowed:
 * 9px inline, or 15px on the wire spine, where it also gets a 3px ring in the
 * page background colour so the spine appears to pass behind it rather than
 * through it.
 *
 * SIZES ARE +2px ON THE TOKEN SPEC, DELIBERATELY. At 7/13 the dots read on a
 * retina mock and disappear on an ordinary screen — they are the only colour
 * the feed has, and a semantic signal that vanishes is not a signal. Two pixels
 * is enough to survive without turning into a bullet: the register is still a
 * quiet mark beside a row, not a status light.
 */
interface Props {
  semantic: KindSemantic
  /** 'spine' = 15px with the bg ring; 'inline' = plain 9px. */
  variant?: 'inline' | 'spine'
}

export default function KindDot({ semantic, variant = 'inline' }: Props) {
  if (variant === 'spine') {
    return (
      <span
        aria-hidden="true"
        className={[
          'block h-[15px] w-[15px] rounded-full',
          'ring-[3px] ring-light-bg dark:ring-dark-bg',
          DOT_BG[semantic],
        ].join(' ')}
      />
    )
  }

  return (
    <span
      aria-hidden="true"
      className={`block h-[9px] w-[9px] rounded-full ${DOT_BG[semantic]}`}
    />
  )
}
