import React from 'react';

const InfoPanel = ({ children }) => {
    return (
        <div style={{
            position: 'fixed',
            bottom: '10px',
            right: '10px',
            padding: '15px',
            borderRadius: '8px',
            color: 'white',
            zIndex: 1,
            maxWidth: '320px',
        }}>
            {children}
        </div>
    );
};

export default InfoPanel;
