/**
 * Installation d'un JAR de serveur depuis une source officielle.
 *
 * Aucune adresse n'est saisissable : on choisit une source connue et une
 * version. Un champ « URL du JAR » ferait du panneau un outil de téléchargement
 * arbitraire tournant avec les droits du service.
 *
 * Les listes de versions sont longues (Mojang en publie des centaines) : les
 * préversions sont masquées par défaut, car ce n'est presque jamais ce qu'on
 * cherche pour un serveur qui accueille des joueurs.
 */

import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { PackagePlus } from 'lucide-react'
import { api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'
import { formatBytes } from '@/lib/format'
import { Card, CardHeader, Checkbox, Field, Select } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'

interface VersionInstallerProps {
  serverId: number
  serverName: string
  running: boolean
  currentJar: string | null
  onInstalled: () => void
}

export function VersionInstaller({
  serverId,
  serverName,
  running,
  currentJar,
  onInstalled,
}: VersionInstallerProps) {
  const push = useToasts((state) => state.push)
  const [source, setSource] = useState('vanilla')
  const [version, setVersion] = useState('')
  const [showPre, setShowPre] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const sources = useQuery({
    queryKey: ['download-sources'],
    queryFn: () => api.downloads.sources(),
    staleTime: Infinity,
  })

  const versions = useQuery({
    queryKey: ['download-versions', source],
    queryFn: () => api.downloads.versions(source),
    // Les catalogues changent rarement, et chaque appel sort sur Internet.
    staleTime: 10 * 60_000,
    retry: false,
  })

  const install = useMutation({
    mutationFn: () => api.downloads.install(serverId, source, version),
    onSuccess: (result) => {
      push({
        kind: 'success',
        title: `${result.file} installé`,
        detail: `${formatBytes(result.size_bytes)} — le serveur démarrera sur cette version.`,
      })
      setConfirming(false)
      onInstalled()
    },
  })

  const listed = (versions.data ?? []).filter(
    (item) => showPre || item.channel === 'release',
  )

  return (
    <Card>
      <CardHeader
        title="Version du serveur"
        subtitle="Téléchargée depuis la source officielle, empreinte vérifiée."
      />

      <div className="space-y-4 px-5 py-4">
        {running ? (
          <p className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-3.5 py-2.5 text-xs text-amber-100">
            Le serveur tourne : arrêter le serveur avant de changer sa version.
          </p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Source">
            <Select
              value={source}
              disabled={running}
              onChange={(event) => {
                setSource(event.target.value)
                setVersion('')
              }}
            >
              {(sources.data ?? []).map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Version"
            hint={versions.isLoading ? 'Chargement du catalogue…' : undefined}
          >
            <Select
              value={version}
              disabled={running || versions.isLoading || listed.length === 0}
              onChange={(event) => setVersion(event.target.value)}
            >
              <option value="">— Choisir —</option>
              {listed.slice(0, 200).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                  {item.channel !== 'release' ? ' (préversion)' : ''}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Checkbox
          label="Afficher les préversions"
          hint="Instables : à réserver aux tests."
          checked={showPre}
          onChange={(event) => setShowPre(event.target.checked)}
        />

        <ErrorPanel error={versions.error} />

        <Button
          variant="primary"
          icon={<PackagePlus className="size-4" />}
          disabled={running || !version}
          onClick={() => setConfirming(true)}
        >
          Installer
        </Button>

        {currentJar ? (
          <p className="truncate text-xs text-slate-600">Fichier actuel : {currentJar}</p>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirming}
        title={`Installer la version ${version} ?`}
        consequence={
          `Le JAR est téléchargé dans le dossier du serveur et sélectionné pour les prochains ` +
          `démarrages. Le fichier actuel n'est pas supprimé. Changer de version sans sauvegarde ` +
          `récente peut rendre un monde illisible : vérifier l'onglet Sauvegardes avant.`
        }
        confirmLabel="Installer"
        danger
        requireTyping={serverName}
        loading={install.isPending}
        error={install.error}
        onConfirm={() => install.mutate()}
        onClose={() => setConfirming(false)}
      />
    </Card>
  )
}
