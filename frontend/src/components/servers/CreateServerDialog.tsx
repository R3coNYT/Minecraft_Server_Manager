/**
 * Ajout d'un serveur, en deux temps : analyse du dossier, puis confirmation.
 *
 * L'analyse **propose** et l'utilisateur **dispose** : chaque champ déduit reste
 * modifiable. C'est ce qui permet de gérer un fork exotique ou une arborescence
 * inhabituelle sans que le panneau ne s'y oppose.
 */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderSearch, Info } from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys, useLaunchers } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatBytes, CAPABILITY_LABELS } from '@/lib/format'
import type { Detection } from '@/lib/types'
import { Dialog } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Badge, Checkbox, Field, Input, Select } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'

interface CreateServerDialogProps {
  open: boolean
  onClose: () => void
}

export function CreateServerDialog({ open, onClose }: CreateServerDialogProps) {
  const queryClient = useQueryClient()
  const { data: launchers } = useLaunchers()
  const push = useToasts((state) => state.push)

  const [directory, setDirectory] = useState('')
  const [name, setName] = useState('')
  const [launcherKey, setLauncherKey] = useState('jar')
  const [jarPath, setJarPath] = useState('')
  const [scriptPath, setScriptPath] = useState('')
  const [memoryMax, setMemoryMax] = useState('4096')
  const [acceptEula, setAcceptEula] = useState(false)
  const [detection, setDetection] = useState<Detection | null>(null)

  const reset = () => {
    setDirectory('')
    setName('')
    setLauncherKey('jar')
    setJarPath('')
    setScriptPath('')
    setMemoryMax('4096')
    setAcceptEula(false)
    setDetection(null)
  }

  const detect = useMutation({
    mutationFn: () => api.servers.detect(directory.trim()),
    onSuccess: (result) => {
      setDetection(result)
      if (result.launcher_key) setLauncherKey(result.launcher_key)
      if (result.jar_path) setJarPath(result.jar_path)
      if (result.script_path) setScriptPath(result.script_path)
      if (!name) {
        const parts = result.directory.split(/[\\/]/).filter(Boolean)
        setName(parts[parts.length - 1] ?? '')
      }
    },
  })

  const create = useMutation({
    mutationFn: () =>
      api.servers.create({
        name: name.trim(),
        directory: directory.trim(),
        launcher_key: launcherKey,
        server_type: detection?.server_type ?? 'UNKNOWN',
        minecraft_version: detection?.minecraft_version ?? null,
        settings: {
          ...(launcherKey === 'jar' && jarPath ? { jar_path: jarPath } : {}),
          ...(launcherKey !== 'jar' && scriptPath ? { script_path: scriptPath } : {}),
          ...(memoryMax ? { memory_max_mb: Number(memoryMax) } : {}),
          auto_accept_eula: acceptEula,
        },
      }),
    onSuccess: (server) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
      push({ kind: 'success', title: `« ${server.name} » ajouté` })
      reset()
      onClose()
    },
  })

  const needsJar = launcherKey === 'jar'

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset()
        onClose()
      }}
      title="Ajouter un serveur"
      description="Indiquer le dossier du serveur ; MSM analyse son contenu et propose une configuration."
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => {
              reset()
              onClose()
            }}
          >
            Annuler
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!name.trim() || !directory.trim()}
            onClick={() => create.mutate()}
          >
            Ajouter
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label="Dossier du serveur"
          hint="Chemin absolu, par exemple /data/minecraft/survie ou C:\\serveurs\\survie"
        >
          <div className="flex gap-2">
            <Input
              value={directory}
              onChange={(event) => setDirectory(event.target.value)}
              placeholder="/data/minecraft/survie"
              spellCheck={false}
            />
            <Button
              variant="secondary"
              icon={<FolderSearch className="size-4" />}
              loading={detect.isPending}
              disabled={!directory.trim()}
              onClick={() => detect.mutate()}
            >
              Analyser
            </Button>
          </div>
        </Field>

        <ErrorPanel error={detect.error} />

        {detection ? (
          <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">
                {detection.server_type}
              </Badge>
              {detection.minecraft_version ? (
                <Badge>Minecraft {detection.minecraft_version}</Badge>
              ) : null}
              {detection.port ? <Badge>Port {detection.port}</Badge> : null}
              {detection.capabilities.map((capability) => (
                <Badge key={capability}>{CAPABILITY_LABELS[capability] ?? capability}</Badge>
              ))}
            </div>

            {detection.jars.length > 0 ? (
              <p className="text-xs text-slate-500">
                {detection.jars.length} fichier(s) .jar :{' '}
                {detection.jars
                  .slice(0, 3)
                  .map((jar) => `${jar.name} (${formatBytes(jar.size_bytes)})`)
                  .join(', ')}
              </p>
            ) : null}

            {detection.notes.map((note) => (
              <p key={note} className="flex items-start gap-1.5 text-xs text-amber-300/90">
                <Info className="mt-0.5 size-3 shrink-0" />
                {note}
              </p>
            ))}
          </div>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Nom affiché">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Serveur Survie"
            />
          </Field>

          <Field label="Mode de démarrage">
            <Select value={launcherKey} onChange={(event) => setLauncherKey(event.target.value)}>
              {(launchers ?? []).map((launcher) => (
                <option
                  key={launcher.key}
                  value={launcher.key}
                  disabled={launcher.unavailable_reason !== null}
                >
                  {launcher.label}
                  {launcher.unavailable_reason ? ' — indisponible' : ''}
                </option>
              ))}
            </Select>
          </Field>

          {needsJar ? (
            <Field label="Fichier JAR" hint="Relatif au dossier du serveur">
              <Input
                value={jarPath}
                onChange={(event) => setJarPath(event.target.value)}
                placeholder="server.jar"
                spellCheck={false}
              />
            </Field>
          ) : (
            <Field label="Script de démarrage" hint="Relatif au dossier du serveur">
              <Input
                value={scriptPath}
                onChange={(event) => setScriptPath(event.target.value)}
                placeholder="run.sh"
                spellCheck={false}
              />
            </Field>
          )}

          <Field label="Mémoire maximale (Mo)">
            <Input
              type="number"
              min={512}
              step={512}
              value={memoryMax}
              onChange={(event) => setMemoryMax(event.target.value)}
            />
          </Field>
        </div>

        <Checkbox
          label="Accepter automatiquement le CLUF Minecraft"
          hint="MSM passera eula.txt à true au premier démarrage. En cochant, vous acceptez le contrat de licence de Minecraft."
          checked={acceptEula}
          onChange={(event) => setAcceptEula(event.target.checked)}
        />

        <ErrorPanel error={create.error} />
      </div>
    </Dialog>
  )
}
