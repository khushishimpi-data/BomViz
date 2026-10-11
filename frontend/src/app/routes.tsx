import type { RouteObject } from 'react-router';

import { LoginPage } from '@/features/auth/LoginPage';
import { RegisterPage } from '@/features/auth/RegisterPage';
import { RequireAuth } from '@/features/auth/RequireAuth';
import { BomDetailPage } from '@/features/boms/BomDetailPage';
import { BomsPage } from '@/features/boms/BomsPage';
import { UploadBomPage } from '@/features/boms/UploadBomPage';
import { ProductDetailPage } from '@/features/products/ProductDetailPage';
import { ProductsPage } from '@/features/products/ProductsPage';
import { MembersPage } from '@/features/workspaces/MembersPage';
import { WorkspaceOverviewPage } from '@/features/workspaces/WorkspaceOverviewPage';
import { WorkspacesPage } from '@/features/workspaces/WorkspacesPage';

import { AppLayout } from './layouts/AppLayout';
import { HomePage } from './pages/HomePage';
import { NotFoundPage } from './pages/NotFoundPage';

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'login', element: <LoginPage /> },
      { path: 'register', element: <RegisterPage /> },
      {
        element: <RequireAuth />,
        children: [
          { path: 'workspaces', element: <WorkspacesPage /> },
          {
            path: 'workspaces/:workspaceId',
            children: [
              { index: true, element: <WorkspaceOverviewPage /> },
              { path: 'members', element: <MembersPage /> },
              { path: 'products', element: <ProductsPage /> },
              { path: 'products/:productId', element: <ProductDetailPage /> },
              { path: 'boms', element: <BomsPage /> },
              { path: 'boms/new', element: <UploadBomPage /> },
              { path: 'boms/:bomId', element: <BomDetailPage /> },
            ],
          },
        ],
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
