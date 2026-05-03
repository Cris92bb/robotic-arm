import React, { useEffect, useState } from 'react';
import './App.css';

function App() {
  const [telemetry, setTelemetry] = useState({});
  const bridgeIp = import.meta.env.VITE_BRIDGE_IP || '127.0.0.1';

  useEffect(() => {
    const eventSource = new EventSource(`http://${bridgeIp}:8081/telemetry`);

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setTelemetry(data);
    };

    eventSource.onerror = (error) => {
      console.error('EventSource failed:', error);
      eventSource.close();
    };

    return () => {
      eventSource.close();
    };
  }, [bridgeIp]);

  const sendCommand = async (command) => {
    try {
      await fetch(`http://${bridgeIp}:8081/command`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ command }),
      });
    } catch (error) {
      console.error('Error sending command:', error);
    }
  };

  return (
    <div className="App">
      <h1>Robotic Arm Dashboard</h1>
      <div className="dashboard">
        <div className="controls">
          <button onClick={() => sendCommand('rest')}>Rest</button>
          <button onClick={() => sendCommand('calibrate')}>Calibrate</button>
        </div>

        <div className="telemetry-board">
          <h2>Telemetry</h2>
          <p><strong>Pan Angle:</strong> {telemetry.pan?.toFixed(2) ?? '--'}</p>
          <p><strong>Tilt Angle:</strong> {telemetry.tilt?.toFixed(2) ?? '--'}</p>
          <p><strong>Target X:</strong> {telemetry.target_x?.toFixed(2) ?? '--'}</p>
          <p><strong>Target Y:</strong> {telemetry.target_y?.toFixed(2) ?? '--'}</p>
          <p><strong>Error X:</strong> {telemetry.error_x?.toFixed(2) ?? '--'}</p>
          <p><strong>Error Y:</strong> {telemetry.error_y?.toFixed(2) ?? '--'}</p>
          <p><strong>Target ID:</strong> {telemetry.target_id ?? '--'}</p>
        </div>

        <div className="video-stream">
          <h2>Video Stream</h2>
          <p><i>Streaming via Go Bridge proxy</i></p>
          <div style={{ width: '640px', height: '480px', backgroundColor: '#ddd', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
             <img src={`http://${bridgeIp}:8081/video`} alt="Robot View" style={{ width: '100%', height: '100%', objectFit: 'cover' }} onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'block'; }} />
             <span style={{ display: 'none' }}>Video stream unavailable</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
