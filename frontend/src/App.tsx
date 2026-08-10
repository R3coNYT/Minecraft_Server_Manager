/** Routage et garde d'authentification. */

import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useMe } from '@/hooks/useApi'
import { realtime } from '@/ws/client'
import { AppShell } from '@/components/layout/AppShell'
import { LoadingBlock } from '@/components/ui/primitives'
import { LoginPage } from '@/pages/LoginPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { AuditPage } from '@/pages/AuditPage'
import { UsersPage } from '@/pages/UsersPage'
import { ServerLayout } from '@/pages/ServerLayout'
import { OverviewPage } from '@/pages/server/OverviewPage'
import { ConsolePage } from '@/pages/server/ConsolePage'
import { PlayersPage } from '@/pages/server/PlayersPage'
import { ComingSoonPage } from '@/pages/server/ComingSoonPage'

/**
 * Garde d'accès.
 *
 * Le WebSocket n'est ouvert **qu'une fois la session confirmée** : le tenter
 * avant produirait une fermeture 4401 immédiate et une boucle de reconnexion
 * inutile sur l'écran de connexion.
 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { data: me, isLoading } = useMe()
  const location = useLocation()

  // La dépendance porte sur l'identifiant, pas sur l'objet : `useMe` renvoie une
  // nouvelle référence à chaque rechargement du cache, ce qui refermerait puis
  // rouvrirait la connexion sans raison.
  const userId = me?.id

  useEffect(() => {
    if (userId === undefined) return undefined
    realtime.connect()
    return () => realtime.close()
  }, [userId])

  if (isLoading) return <LoadingBlock />
  if (!me) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="users" element={<UsersPage />} />

        <Route path="servers/:serverId" element={<ServerLayout />}>
          <Route index element={<OverviewPage />} />
          <Route path="console" element={<ConsolePage />} />
          <Route path="players" element={<PlayersPage />} />
          <Route
            path="mods"
            element={
              <ComingSoonPage
                title="Gestion des mods"
                phase="phase 3"
                description="Lister, téléverser, activer ou désactiver les fichiers du dossier mods/. La désactivation renommera le fichier plutôt que de le supprimer."
              />
            }
          />
          <Route
            path="plugins"
            element={
              <ComingSoonPage
                title="Gestion des plugins"
                phase="phase 3"
                description="Même principe que les mods, appliqué au dossier plugins/ des serveurs compatibles Bukkit."
              />
            }
          />
          <Route
            path="configs"
            element={
              <ComingSoonPage
                title="Éditeur de configurations"
                phase="phase 3"
                description="Édition des fichiers .json, .toml, .yaml et .properties avec coloration et validation syntaxique, plus une interface dédiée à server.properties."
              />
            }
          />
          <Route
            path="events"
            element={
              <ComingSoonPage
                title="Événements"
                phase="phase 4"
                description="Messages globaux, titres, dons d'objets et suites d'actions programmées, à déclencher en un clic."
              />
            }
          />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
