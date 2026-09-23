/**
 * Applique la langue du panneau à toute l'interface.
 *
 * La langue est lue sur `/api/v1/ui`, accessible sans être connecté : l'écran de
 * connexion s'affiche déjà dans la bonne langue. En attendant la réponse, la
 * dernière langue connue de ce navigateur est utilisée.
 *
 * Changer de langue remonte l'arbre sous cette frontière (clé React) : chaque
 * texte est recalculé sans qu'aucun composant n'ait à s'abonner. Le cache des
 * requêtes, lui, survit — seules les données dont le texte vient du serveur sont
 * rechargées, par l'écran des réglages.
 */

import { useEffect, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useLanguage } from '@/i18n'

export const UI_SETTINGS_KEY = ['ui-settings'] as const

export function LanguageBoundary({ children }: { children: ReactNode }) {
  const language = useLanguage((state) => state.language)
  const setLanguage = useLanguage((state) => state.setLanguage)

  const { data } = useQuery({
    queryKey: UI_SETTINGS_KEY,
    queryFn: () => api.ui.get(),
    staleTime: Infinity,
  })

  useEffect(() => {
    if (data?.language && data.language !== language) setLanguage(data.language)
  }, [data?.language, language, setLanguage])

  return <div key={language} className="contents">{children}</div>
}
