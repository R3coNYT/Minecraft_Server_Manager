/**
 * Traduction de l'interface.
 *
 * L'anglais est la langue source (`en.ts`) ; `fr.ts` en est la traduction, et
 * TypeScript refuse une clé manquante ou superflue. La langue est un réglage
 * global du panneau, lu sur `/api/v1/ui` : le backend produit ses propres textes
 * (erreurs, console, journal d'audit) dans la même langue.
 *
 * `t()` est une simple fonction, utilisable partout — composants, formatage,
 * notifications. Changer de langue remonte l'arbre de l'application (voir
 * `LanguageBoundary`) : aucun composant n'a à s'abonner lui-même.
 */

import { create } from 'zustand'
import { en, type MessageKey } from './en'
import { fr } from './fr'

export type { MessageKey } from './en'
export type Language = 'en' | 'fr'
export type Params = Record<string, string | number>

const CATALOGS: Record<Language, Record<MessageKey, string>> = { en, fr }
const STORAGE_KEY = 'msm.language'

/** Dernière langue connue : évite d'afficher l'anglais le temps de lire le réglage. */
function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'en' || stored === 'fr') return stored
  } catch {
    // Stockage indisponible (navigation privée) : l'anglais par défaut.
  }
  return 'en'
}

interface LanguageState {
  language: Language
  setLanguage: (language: Language) => void
}

export const useLanguage = create<LanguageState>((set) => ({
  language: initialLanguage(),
  setLanguage: (language) => {
    try {
      localStorage.setItem(STORAGE_KEY, language)
    } catch {
      // Sans stockage, la langue sera simplement relue au prochain chargement.
    }
    document.documentElement.lang = language
    set({ language })
  },
}))

document.documentElement.lang = useLanguage.getState().language

function interpolate(text: string, params?: Params): string {
  if (!params) return text
  return text.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  )
}

/** Texte de l'interface dans la langue courante. */
export function t(key: MessageKey, params?: Params): string {
  const language = useLanguage.getState().language
  return interpolate(CATALOGS[language][key] ?? en[key] ?? key, params)
}

type PluralBase<K> = K extends `${infer Base}_one` ? Base : never
export type PluralKey = PluralBase<MessageKey>

/**
 * Accord en nombre : `{clé}_one` ou `{clé}_other`.
 *
 * Le français met zéro au singulier (« 0 joueur connecté »), l'anglais au
 * pluriel (« 0 players online ») : la règle dépend de la langue.
 */
export function tn(key: PluralKey, count: number, params?: Params): string {
  const language = useLanguage.getState().language
  const singular = language === 'fr' ? Math.abs(count) < 2 : count === 1
  const suffix = singular ? '_one' : '_other'
  return t(`${key}${suffix}` as MessageKey, { count, ...params })
}

/** Locale des dates et des nombres. */
export function locale(): string {
  return useLanguage.getState().language === 'fr' ? 'fr-FR' : 'en-GB'
}
