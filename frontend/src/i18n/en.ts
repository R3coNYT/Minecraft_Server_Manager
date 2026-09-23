/** Textes anglais, langue source de l'interface. */

import { SECTIONS } from './catalog'

type Section = (typeof SECTIONS)[number]
type Merge<U> = (U extends unknown ? (value: U) => void : never) extends (value: infer I) => void
  ? I
  : never

type English = Merge<Section['en']>

export const en = Object.assign({}, ...SECTIONS.map((section) => section.en)) as English

export type MessageKey = keyof English
