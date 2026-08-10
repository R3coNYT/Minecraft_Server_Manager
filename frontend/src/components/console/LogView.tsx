/**
 * Affichage de la console.
 *
 * **Pas de virtualisation.** Une première version utilisait une liste virtualisée ;
 * combinée à la mesure dynamique des hauteurs, elle calculait mal la taille
 * totale et le défilement automatique n'atteignait jamais le bas — la console
 * restait figée sur d'anciennes lignes alors que le flux continuait d'arriver.
 * Le rendu direct d'une fenêtre bornée est ici plus simple *et* plus sûr : les
 * lignes sont de simples éléments de texte, et le navigateur en affiche
 * plusieurs milliers sans difficulté.
 *
 * Le défilement automatique se **désactive dès que l'utilisateur remonte** et se
 * réactive quand il redescend. Rien n'est plus agaçant qu'une console qui ramène
 * de force en bas pendant qu'on lit une trace d'erreur.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Eraser, Search, X } from 'lucide-react'
import type { LogLevel, LogLine } from '@/lib/types'
import { cn } from '@/lib/cn'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/primitives'

/** Nombre de lignes réellement rendues ; au-delà, seules les plus récentes. */
const RENDER_WINDOW = 2000

/** Distance au bas en dessous de laquelle on considère l'utilisateur « en bas ». */
const STICKY_THRESHOLD_PX = 48

const LEVEL_STYLES: Record<LogLevel, string> = {
  TRACE: 'text-slate-600',
  DEBUG: 'text-slate-500',
  INFO: 'text-slate-300',
  WARN: 'text-amber-300',
  ERROR: 'text-red-300',
  FATAL: 'text-red-200 font-semibold',
  RAW: 'text-slate-400',
}

const SOURCE_STYLES: Record<LogLine['source'], string> = {
  stdout: '',
  stderr: 'text-red-300',
  msm: 'text-sky-300 italic',
  command: 'text-emerald-300 font-medium',
}

/**
 * Heure affichée en début de ligne.
 *
 * Les lignes du serveur portent leur propre horodatage ; celles émises par MSM
 * n'en ont pas — on prend alors l'heure de réception plutôt que d'afficher des
 * tirets, pour que la chronologie reste lisible d'un bout à l'autre.
 */
function lineTime(line: LogLine): string {
  if (line.server_time) return line.server_time
  const date = new Date(line.ts)
  return Number.isNaN(date.getTime())
    ? '--:--:--'
    : date.toLocaleTimeString('fr-FR', { hour12: false })
}

interface LogViewProps {
  lines: LogLine[]
  missed: number
  onClear: () => void
  emptyHint?: string
}

export function LogView({ lines, missed, onClear, emptyHint }: LogViewProps) {
  const scrollerRef = useRef<HTMLDivElement>(null)
  const [stickToBottom, setStickToBottom] = useState(true)
  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)

  const filtered = useMemo(() => {
    if (!query.trim()) return lines
    const needle = query.toLowerCase()
    return lines.filter((line) => line.text.toLowerCase().includes(needle))
  }, [lines, query])

  const visible = useMemo(
    () => (filtered.length > RENDER_WINDOW ? filtered.slice(-RENDER_WINDOW) : filtered),
    [filtered],
  )

  // `useLayoutEffect` : le défilement est appliqué avant la peinture, sinon
  // chaque nouvelle ligne produirait un saut visible.
  useLayoutEffect(() => {
    if (!stickToBottom) return
    const element = scrollerRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [visible, stickToBottom])

  const onScroll = useCallback(() => {
    const element = scrollerRef.current
    if (!element) return
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    setStickToBottom(distance <= STICKY_THRESHOLD_PX)
  }, [])

  // Revenir en bas quand on ferme la recherche : le contenu a changé de taille.
  useEffect(() => {
    if (!searchOpen) setStickToBottom(true)
  }, [searchOpen])

  const jumpToBottom = () => {
    setStickToBottom(true)
    const element = scrollerRef.current
    if (element) element.scrollTop = element.scrollHeight
  }

  const hidden = filtered.length - visible.length

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2">
        {searchOpen ? (
          <div className="flex flex-1 items-center gap-2">
            <Search className="size-3.5 shrink-0 text-slate-500" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  setQuery('')
                  setSearchOpen(false)
                }
              }}
              placeholder="Filtrer les lignes affichées…"
              className="flex-1 bg-transparent text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none"
            />
            <span className="text-[11px] tabular-nums text-slate-500">
              {filtered.length} / {lines.length}
            </span>
            <button
              onClick={() => {
                setQuery('')
                setSearchOpen(false)
              }}
              className="rounded p-0.5 text-slate-500 hover:text-slate-200"
              aria-label="Fermer la recherche"
            >
              <X className="size-3.5" />
            </button>
          </div>
        ) : (
          <>
            <span className="text-[11px] tabular-nums text-slate-500">
              {lines.length} ligne{lines.length > 1 ? 's' : ''}
            </span>
            {missed > 0 ? (
              <span
                className="rounded bg-amber-950/60 px-2 py-0.5 text-[11px] text-amber-300"
                title="Le tampon d'historique a été dépassé : ces lignes ne sont plus disponibles."
              >
                {missed} ligne{missed > 1 ? 's' : ''} perdue{missed > 1 ? 's' : ''}
              </span>
            ) : null}
            <div className="flex-1" />
            <Button
              size="sm"
              variant="ghost"
              icon={<Search className="size-3.5" />}
              onClick={() => setSearchOpen(true)}
            >
              Rechercher
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={<Eraser className="size-3.5" />}
              onClick={onClear}
            >
              Effacer
            </Button>
          </>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollerRef}
          onScroll={onScroll}
          className="h-full overflow-auto bg-slate-950/70 px-3 py-2"
        >
          {visible.length === 0 ? (
            <EmptyState
              title={query ? 'Aucune ligne ne correspond' : 'Console vide'}
              description={
                query
                  ? 'Modifier le filtre pour afficher davantage de lignes.'
                  : (emptyHint ?? 'Les lignes apparaîtront ici dès le démarrage du serveur.')
              }
            />
          ) : (
            <>
              {hidden > 0 ? (
                <p className="pb-2 text-[11px] text-slate-600">
                  {hidden} ligne{hidden > 1 ? 's' : ''} plus ancienne
                  {hidden > 1 ? 's' : ''} masquée{hidden > 1 ? 's' : ''}
                </p>
              ) : null}
              {visible.map((line) => (
                <div
                  key={line.seq}
                  className="console-line flex gap-2.5 hover:bg-slate-900/60"
                >
                  <span className="shrink-0 select-none tabular-nums text-slate-600">
                    {lineTime(line)}
                  </span>
                  <span
                    className={cn(
                      'min-w-0 flex-1 whitespace-pre-wrap break-words',
                      LEVEL_STYLES[line.level],
                      SOURCE_STYLES[line.source],
                    )}
                  >
                    {line.text}
                  </span>
                </div>
              ))}
            </>
          )}
        </div>

        {!stickToBottom && visible.length > 0 ? (
          <button
            onClick={jumpToBottom}
            className="absolute bottom-3 right-4 flex items-center gap-1.5 rounded-full bg-slate-800 px-3 py-1.5 text-xs text-slate-200 shadow-lg ring-1 ring-slate-700 transition-colors hover:bg-slate-700"
          >
            <ArrowDown className="size-3.5" />
            Suivre le flux
          </button>
        ) : null}
      </div>
    </div>
  )
}
