/** Traduction française de l'interface. */

import { SECTIONS } from './catalog'
import type { MessageKey } from './en'

export const fr = Object.assign({}, ...SECTIONS.map((section) => section.fr)) as Record<
  MessageKey,
  string
>
