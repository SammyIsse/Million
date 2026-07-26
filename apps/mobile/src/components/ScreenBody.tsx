import React from 'react';
import { StyleSheet, useWindowDimensions, View, type StyleProp, type ViewStyle } from 'react-native';
import { useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import { useHeaderHeight } from '@react-navigation/elements';

/**
 * Giver scroll-children en begrænset højde.
 * Uden det kan Yoga lade FlatList/ScrollView vokse med indholdet,
 * så indholdet klippes af parent uden at der kan scrolls.
 */

/** Tab-skærme (Home, Cart, Settings). */
export function TabScreenBody({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const { height: windowHeight } = useWindowDimensions();
  const headerHeight = useHeaderHeight();
  const tabBarHeight = useBottomTabBarHeight();
  const bodyHeight = Math.max(200, windowHeight - headerHeight - tabBarHeight);

  return (
    <View style={[styles.fill, { height: bodyHeight, maxHeight: bodyHeight }, style]}>
      {children}
    </View>
  );
}

/** Stack-skærme (ProductDetail, Category, Search, …). */
export function StackScreenBody({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const { height: windowHeight } = useWindowDimensions();
  const headerHeight = useHeaderHeight();
  const bodyHeight = Math.max(200, windowHeight - headerHeight);

  return (
    <View style={[styles.fill, { height: bodyHeight, maxHeight: bodyHeight }, style]}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: {
    flex: 1,
    minHeight: 0,
    minWidth: 0,
  },
});
