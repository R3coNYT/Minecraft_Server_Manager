/**
 * Applique la langue à toute l'interface.
 *
 * Chaque compte peut choisir sa langue ; à défaut, c'est celle du panneau, lue
 * sur `/api/v1/ui`, accessible sans être connecté : l'écran de connexion
 * s'affiche déjà dans la bonne langue. En attendant la réponse, la dernière
 * langue connue de ce navigateur est utilisée.
 *
 * Changer de langue remonte l'arbre sous cette frontière (clé React) : chaque
 * texte est recalculé sans qu'aucun composant n'ait à s'abonner. Le cache des
 * requêtes, lui, survit — seules les données dont le texte vient du serveur sont
 * rechargées, par l'écran qui change la langue.
 */

import { useEffect, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useMe } from '@/hooks/useApi'
import { useLanguage, type Language } from '@/i18n'

export const UI_SETTINGS_KEY = ['ui-settings'] as const

export function LanguageBoundary({ children }: { children: ReactNode }) {
  const language = useLanguage((state) => state.language)
  const setLanguage = useLanguage((state) => state.setLanguage)
  const { data: me } = useMe()

  const { data } = useQuery({
    queryKey: UI_SETTINGS_KEY,
    queryFn: () => api.ui.get(),
    staleTime: Infinity,
  })

  // La langue du compte prime sur celle du panneau.
  const wanted = (me?.language as Language | null | undefined) ?? data?.language

  useEffect(() => {
    if (wanted && wanted !== language) setLanguage(wanted)
  }, [wanted, language, setLanguage])

  return <div key={language} className="contents">{children}</div>
}
