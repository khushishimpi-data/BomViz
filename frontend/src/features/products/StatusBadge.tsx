import { Badge } from '@/components/ui/badge';

import {
  PRODUCT_STATUS_LABELS,
  REVISION_STATUS_LABELS,
  type ProductStatus,
  type RevisionStatus,
} from './api';

export type StatusBadgeProps =
  { kind: 'product'; status: ProductStatus } | { kind: 'revision'; status: RevisionStatus };

/** Status as text in a badge; colour only reinforces the label. */
export function StatusBadge(props: StatusBadgeProps) {
  const label =
    props.kind === 'product'
      ? PRODUCT_STATUS_LABELS[props.status]
      : REVISION_STATUS_LABELS[props.status];
  const variant =
    props.status === 'DRAFT' ? 'secondary' : props.status === 'OBSOLETE' ? 'outline' : 'success';
  return <Badge variant={variant}>{label}</Badge>;
}
