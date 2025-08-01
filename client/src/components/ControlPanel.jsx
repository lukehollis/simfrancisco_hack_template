import React from 'react';

const ControlPanel = ({ children }) => {
    return (
        <div style={{
            position: 'absolute',
            top: '20px',
            left: '20px',
            background: 'rgba(0, 0, 0, 0.7)',
            padding: '15px',
            borderRadius: '8px',
            color: 'white',
            zIndex: 1,
            width: '240px',
        }}>
            {children}
        </div>
    );
};

export default ControlPanel;
