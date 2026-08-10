/**
 * Onglet prévu mais non encore implémenté.
 *
 * Afficher clairement « prévu en phase N » vaut mieux qu'une page blanche ou
 * qu'un onglet masqué : l'utilisateur sait que la fonctionnalité existe dans la
 * feuille de route, et ne cherche pas un réglage introuvable.
 */

import { Construction } from 'lucide-react'
import { Card, EmptyState } from '@/components/ui/primitives'

interface ComingSoonPageProps {
  title: string
  phase: string
  description: string
}

export function ComingSoonPage({ title, phase, description }: ComingSoonPageProps) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState
            icon={<Construction className="size-8" />}
            title={`${title} — prévu en ${phase}`}
            description={description}
          />
        </Card>
      </div>
    </div>
  )
}
