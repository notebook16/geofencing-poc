import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Clarity from '@microsoft/clarity'
import './index.css'
import App from './App.jsx'

Clarity.init('xrfosw90gq')

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
