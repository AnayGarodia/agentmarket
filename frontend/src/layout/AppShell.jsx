import { useLayoutEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import Sidebar from './Sidebar'
import MobileNav from './MobileNav'
import Toast from '../ui/Toast'
import OnboardingWizard from '../features/onboarding/OnboardingWizard'
import { useMarket } from '../context/MarketContext'
import './AppShell.css'

export default function AppShell({ children }) {
  const market = useMarket()
  const toast = market?.toast
  const location = useLocation()

  // Reset the main scroll container on every route change so pages always
  // open at the top, regardless of where the previous route was scrolled to.
  // useLayoutEffect fires before paint so the user never sees a mid-page state.
  useLayoutEffect(() => {
    document.querySelector('.shell__main')?.scrollTo({ top: 0, behavior: 'instant' })
  }, [location.pathname])

  return (
    <div className="shell">
      <Sidebar />
      <div className="shell__main">
        <AnimatePresence mode="sync">
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="shell__page"
          >
            {children ?? <Outlet />}
          </motion.div>
        </AnimatePresence>
      </div>
      <MobileNav />
      <Toast toast={toast} />
      {market && <OnboardingWizard />}
    </div>
  )
}
