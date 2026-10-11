import { Badge } from '@/components/ui/badge';

import { UPLOAD_STATUS_LABELS, type UploadStatus } from './api';

type BadgeVariant = 'secondary' | 'info' | 'success' | 'destructive';

const STATUS_VARIANTS: Readonly<Record<UploadStatus, BadgeVariant>> = {
  UPLOADED: 'secondary',
  PARSING: 'info',
  PARSED: 'success',
  PARSE_FAILED: 'destructive',
  MAPPED: 'success',
  VALIDATING: 'info',
  VALIDATED: 'success',
  VALIDATION_FAILED: 'destructive',
  IMPORTING: 'info',
  IMPORTED: 'success',
  IMPORT_FAILED: 'destructive',
};

export interface BomStatusBadgeProps {
  status: UploadStatus;
}

/** Upload status as text in a badge; colour only reinforces the label. */
export function BomStatusBadge({ status }: BomStatusBadgeProps) {
  return <Badge variant={STATUS_VARIANTS[status]}>{UPLOAD_STATUS_LABELS[status]}</Badge>;
}
