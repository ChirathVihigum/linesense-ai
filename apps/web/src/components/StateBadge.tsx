import clsx from 'clsx'

import { Icon } from './Icon'
import { TONE_CLASSES, VOCABULARY_LABELS, stateStyle, type StateVocabulary } from './stateStyles'

/** A state rendered as icon + text + colour (never colour alone). */
export function StateBadge({ vocabulary, state }: { vocabulary: StateVocabulary; state: string }) {
  const style = stateStyle(vocabulary, state)
  return (
    <span
      data-state={state}
      className={clsx(
        'inline-flex h-6 items-center gap-1 rounded-full border px-2 text-xs font-medium whitespace-nowrap',
        TONE_CLASSES[style.tone],
      )}
    >
      <Icon name={style.icon} className="h-3.5 w-3.5 shrink-0" />
      <span className="sr-only">{VOCABULARY_LABELS[vocabulary]}: </span>
      <span>{style.label}</span>
    </span>
  )
}
